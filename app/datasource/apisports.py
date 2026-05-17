import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
import httpx
from app.config import get_apisports_config, load_config
from app.datasource.base import Game, GameDetail, DataSource
from app.translator import tr_league, reload_cache

logger = logging.getLogger(__name__)

CN_TZ = timezone(timedelta(hours=8))

BB_API = "https://v1.basketball.api-sports.io/games"
FB_API = "https://v3.football.api-sports.io/fixtures"

STATUS_MAP = {
    "NS": "未开始", "Not Started": "未开始",
    "Q1": "第一节", "Q2": "第二节", "Q3": "第三节", "Q4": "第四节",
    "1H": "上半场", "HT": "中场休息", "2H": "下半场",
    "FT": "已结束", "Game Finished": "已结束", "Match Finished": "已结束",
    "AOT": "加时结束", "AET": "加时结束",
    "PEN": "点球", "SUSP": "中断", "INT": "中断",
    "PST": "延期", "CANC": "已取消", "ABD": "腰斩",
}


class _KeyRotator:
    """API key 轮询器：一个 key 用完自动切下一个，每天重置"""

    def __init__(self, keys: list[str]):
        self._keys = keys
        self._idx = 0
        self._exhausted: dict[int, float] = {}

    def current(self) -> str:
        return self._keys[self._idx] if self._keys else ""

    def mark_exhausted(self):
        import time
        self._exhausted[self._idx] = time.monotonic()
        logger.warning(f"Key {self._idx} exhausted, switching...")
        for _ in range(len(self._keys)):
            self._idx = (self._idx + 1) % len(self._keys)
            if self._idx not in self._exhausted:
                return
        self._exhausted.clear()
        self._idx = 0
        logger.info("All keys exhausted, resetting rotation")

    def is_exhausted(self) -> bool:
        import time
        t = self._exhausted.get(self._idx)
        if t is None:
            return False
        if time.monotonic() - t > 86400:
            del self._exhausted[self._idx]
            return False
        return True


class APISportsDataSource(DataSource):
    """API-Sports 数据源 — 篮球逐节得分 + 足球海量联赛"""

    LIVE_STATUSES = {"第1节", "第2节", "第3节", "第4节",
                     "第一节", "第二节", "第三节", "第四节",
                     "上半场", "下半场", "中场休息", "进行中"}

    def __init__(self):
        keys = get_apisports_config().get("keys", [])
        if not keys:
            raise RuntimeError("No API-Sports keys configured")
        self._rotator = _KeyRotator(keys)
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        key = self._rotator.current()
        if self._client is None or self._client.headers.get("x-apisports-key") != key:
            self._client = httpx.AsyncClient(timeout=15, headers={
                "x-apisports-key": key,
                "Accept": "application/json",
            })
        return self._client

    async def _fetch(self, url: str) -> dict:
        last_err = None
        for _ in range(len(self._rotator._keys)):
            if self._rotator.is_exhausted():
                self._rotator.mark_exhausted()
                continue
            try:
                client = self._get_client()
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    errors = data.get("errors", [])
                    if errors:
                        err_text = str(errors).lower()
                        if any(w in err_text for w in ("quota", "rate", "limit", "exceeded", "requests")):
                            self._rotator.mark_exhausted()
                            continue
                        return data
                    return data
                elif resp.status_code == 429:
                    self._rotator.mark_exhausted()
                    continue
                else:
                    resp.raise_for_status()
            except Exception as e:
                last_err = e
                self._rotator.mark_exhausted()
                continue
        raise last_err or RuntimeError("All API keys exhausted")

    # ── 今日赛程 ──────────────────────────────

    async def get_today_games(self, force: bool = False) -> dict[str, list[Game]]:
        today = datetime.now(CN_TZ).strftime("%Y-%m-%d")

        async def fetch_bb():
            try:
                data = await self._fetch(f"{BB_API}?date={today}&timezone=Asia/Shanghai")
                return self._parse_bb_games(data.get("response", []))
            except Exception as e:
                logger.warning(f"API-Sports basketball failed: {e}")
                return []

        async def fetch_fb():
            try:
                data = await self._fetch(f"{FB_API}?date={today}&timezone=Asia/Shanghai")
                return self._parse_fb_games(data.get("response", []))
            except Exception as e:
                logger.warning(f"API-Sports football failed: {e}")
                return []

        bb, fb = await asyncio.gather(fetch_bb(), fetch_fb())
        # 后台异步翻译缺失的名称
        self._schedule_translation(bb + fb)
        return {"basketball": bb, "football": fb}

    def _schedule_translation(self, games: list[Game]):
        """提交后台翻译任务"""
        leagues = set()
        teams = set()
        for g in games:
            leagues.add(g.league)
            teams.add(g.home_team)
            teams.add(g.away_team)
        # 去掉已缓存的
        from app.translator import _get_cache
        cache = _get_cache()
        league_store = cache.get("league", {})
        team_store = cache.get("team", {})
        missing_l = [n for n in leagues if n and n not in league_store]
        missing_t = [n for n in teams if n and n not in team_store]
        if missing_l or missing_t:
            cfg = load_config()
            api_key = cfg.get("minimax", {}).get("key", "")
            if api_key:
                asyncio.create_task(self._do_translate(api_key, missing_l, missing_t))

    async def _do_translate(self, api_key: str, leagues: list[str], teams: list[str]):
        from app.translator import translate_missing
        if leagues:
            await translate_missing(api_key, leagues, "league")
        if teams:
            await translate_missing(api_key, teams, "team")
        reload_cache()

    # ── 实时比赛 ──────────────────────────────

    async def get_live_games(self, sport_type: str) -> list[Game]:
        games = []
        for g in (await self.get_today_games()).get(sport_type, []):
            if g.status in self.LIVE_STATUSES:
                games.append(g)
        return games

    async def get_game_detail(self, game_id: str, sport_type: str) -> Optional[GameDetail]:
        """从已缓存的 get_today_games 结果查找，避免重复 API 调用"""
        # 如果没缓存，先拉一次
        if self._today_cache is None:
            await self.get_today_games()
        for g in (self._today_cache or {}).get(sport_type, []):
            if g.id == game_id:
                return GameDetail(
                    id=g.id, sport_type=g.sport_type,
                    home_team=g.home_team, away_team=g.away_team,
                    status=g.status, current_quarter=g.current_quarter,
                    home_total=g.home_total, away_total=g.away_total,
                    home_scores=getattr(g, '_home_scores', []),
                    away_scores=getattr(g, '_away_scores', []),
                    raw_data=getattr(g, '_raw_data', {}),
                )
        return None

    # ── 篮球解析 ──────────────────────────────

    def _parse_bb_games(self, games: list) -> list[Game]:
        result = []
        for g in games:
            scores = g.get("scores", {})
            home_s = scores.get("home", {})
            away_s = scores.get("away", {})
            home_q = self._bb_quarters(home_s)
            away_q = self._bb_quarters(away_s)

            teams = g.get("teams", {})
            home_t = teams.get("home", {})
            away_t = teams.get("away", {})
            status = self._bb_status(g.get("status", {}), home_q)
            current_q = len([x for x in home_q if x > 0]) if status not in ("未开始", "已结束") else 0
            if status == "已结束":
                current_q = len(home_q)

            raw_league = g.get("league", {}).get("name", "")
            game = Game(
                id=str(g.get("id", "")),
                sport_type="basketball",
                home_team=home_t.get("name", ""),
                away_team=away_t.get("name", ""),
                status=status,
                current_quarter=current_q,
                home_total=int(home_s.get("total", 0) or 0),
                away_total=int(away_s.get("total", 0) or 0),
                start_time=self._fmt_time(g.get("date", "")),
                league=tr_league(raw_league) or raw_league,
            )
            game._home_scores = home_q
            game._away_scores = away_q
            game._raw_data = g
            result.append(game)
        return result

    def _bb_quarters(self, scores: dict) -> list[int]:
        qs = []
        for key in ("quarter_1", "quarter_2", "quarter_3", "quarter_4"):
            v = scores.get(key)
            qs.append(int(v) if v is not None else 0)
        ot = scores.get("over_time")
        if ot is not None:
            qs.append(int(ot))
        return qs

    def _bb_status(self, status: dict, quarters: list[int]) -> str:
        short = status.get("short", "")
        if short in ("NS",):
            return "未开始"
        if short in ("Q1", "Q2", "Q3", "Q4"):
            return f"第{short[-1]}节"
        if short in ("FT", "AOT"):
            return "已结束"
        if short in ("HT",):
            return "中场休息"
        return STATUS_MAP.get(short, short or "未知")

    # ── 足球解析 ──────────────────────────────

    def _parse_fb_games(self, fixtures: list) -> list[Game]:
        result = []
        for f in fixtures:
            fixture = f.get("fixture", {})
            teams = f.get("teams", {})
            score = f.get("score", {})
            goals = f.get("goals", {})

            home_t = teams.get("home", {})
            away_t = teams.get("away", {})
            status = self._fb_status(fixture.get("status", {}))
            ht = score.get("halftime", {})
            home_q = [int(ht.get("home", 0) or 0)] if ht else []
            away_q = [int(ht.get("away", 0) or 0)] if ht else []

            current_q = 0
            if status == "上半场":
                current_q = 1
            elif status == "下半场":
                current_q = 2
            elif status == "已结束":
                current_q = 2

            raw_league = f.get("league", {}).get("name", "")
            game = Game(
                id=str(fixture.get("id", "")),
                sport_type="football",
                home_team=home_t.get("name", ""),
                away_team=away_t.get("name", ""),
                status=status,
                current_quarter=current_q,
                home_total=int(goals.get("home", 0) or 0),
                away_total=int(goals.get("away", 0) or 0),
                start_time=self._fmt_time(fixture.get("date", "")),
                league=tr_league(raw_league) or raw_league,
            )
            game._home_scores = home_q
            game._away_scores = away_q
            game._raw_data = f
            result.append(game)
        return result

    def _fb_status(self, status: dict) -> str:
        short = status.get("short", "")
        if short in ("NS", "TBD", "PST"):
            return "未开始"
        if short in ("1H",):
            return "上半场"
        if short in ("HT",):
            return "中场休息"
        if short in ("2H",):
            return "下半场"
        if short in ("FT", "AET", "PEN"):
            return "已结束"
        return STATUS_MAP.get(short, short or "未知")

    def _fmt_time(self, date_str: str) -> str:
        if not date_str:
            return ""
        try:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            return dt.astimezone(CN_TZ).strftime("%H:%M")
        except (ValueError, AttributeError):
            return date_str[-8:-3] if len(date_str) >= 8 else date_str
