import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
import httpx
from app.datasource.base import Game, GameDetail, DataSource

logger = logging.getLogger(__name__)

CN_TZ = timezone(timedelta(hours=8))

ESPN_BASE = "http://site.api.espn.com/apis/site/v2/sports"

# 篮球联赛
BASKETBALL_LEAGUES = {
    "nba": f"{ESPN_BASE}/basketball/nba/scoreboard",
    "wnba": f"{ESPN_BASE}/basketball/wnba/scoreboard",
    "ncaam": f"{ESPN_BASE}/basketball/mens-college-basketball/scoreboard",
    "ncaaw": f"{ESPN_BASE}/basketball/womens-college-basketball/scoreboard",
}

# 足球联赛
SOCCER_LEAGUES = {
    "eng.1": "英超", "usa.1": "美职联", "esp.1": "西甲",
    "ita.1": "意甲", "ger.1": "德甲", "fra.1": "法甲",
    "ned.1": "荷甲", "por.1": "葡超",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0",
}

STATUS_MAP = {
    "STATUS_SCHEDULED": "未开始",
    "STATUS_IN_PROGRESS": "进行中",
    "STATUS_HALFTIME": "中场休息",
    "STATUS_END_OF_PERIOD": "节间休息",
    "STATUS_FINAL": "已结束",
    "STATUS_POSTPONED": "延期",
    "STATUS_CANCELED": "已取消",
}


class ESPNDataSource(DataSource):
    """ESPN 公开 API — 免费，提供逐节/半场得分"""

    def __init__(self):
        self._client = httpx.AsyncClient(timeout=15, headers=HEADERS)

    # ── 今日赛程 ──────────────────────────────

    async def get_today_games(self, force: bool = False) -> dict[str, list[Game]]:
        today = datetime.now(CN_TZ).strftime("%Y%m%d")

        async def fetch_bb():
            games = []
            for league_name, url in BASKETBALL_LEAGUES.items():
                try:
                    events = await self._fetch_scoreboard(f"{url}?dates={today}")
                    for e in events:
                        g = self._parse_game(e, "basketball", league_name)
                        if g:
                            games.append(g)
                except Exception as exc:
                    logger.warning(f"ESPN {league_name} failed: {exc}")
            return games

        async def fetch_soccer():
            games = []
            for league_code, league_name in SOCCER_LEAGUES.items():
                try:
                    url = f"{ESPN_BASE}/soccer/{league_code}/scoreboard?dates={today}"
                    events = await self._fetch_scoreboard(url)
                    for e in events:
                        g = self._parse_game(e, "football", league_name)
                        if g:
                            games.append(g)
                except Exception as exc:
                    logger.warning(f"ESPN soccer {league_code} failed: {exc}")
            return games

        bb, fb = await asyncio.gather(fetch_bb(), fetch_soccer())
        return {"basketball": bb, "football": fb}

    # ── 实时比赛 ──────────────────────────────

    async def get_live_games(self, sport_type: str) -> list[Game]:
        today = datetime.now(CN_TZ).strftime("%Y%m%d")
        games = []

        if sport_type == "basketball":
            league_urls = BASKETBALL_LEAGUES
        else:
            league_urls = {k: f"{ESPN_BASE}/soccer/{k}/scoreboard"
                          for k in SOCCER_LEAGUES}

        for league_name, url in league_urls.items():
            try:
                events = await self._fetch_scoreboard(f"{url}?dates={today}")
                for e in events:
                    status = self._parse_event_status(e)
                    if status in ("进行中", "中场休息", "节间休息"):
                        g = self._parse_game(e, sport_type, league_name)
                        if g:
                            games.append(g)
            except Exception:
                continue
        return games

    async def get_game_detail(self, game_id: str, sport_type: str) -> Optional[GameDetail]:
        """从今日赛程中查找详情（linescores 已包含）"""
        result = await self.get_today_games()
        for g in result.get(sport_type, []):
            if g.id == game_id:
                # 重建详情：重新拉取 scoreboard 获取 linescores
                # 这里直接返回 Game 转 GameDetail（已有 linescores）
                return GameDetail(
                    id=g.id,
                    sport_type=g.sport_type,
                    home_team=g.home_team,
                    away_team=g.away_team,
                    status=g.status,
                    current_quarter=g.current_quarter,
                    home_total=g.home_total,
                    away_total=g.away_total,
                    home_scores=getattr(g, '_home_scores', []),
                    away_scores=getattr(g, '_away_scores', []),
                    raw_data={},
                )
        return None

    # ── 内部方法 ──────────────────────────────

    async def _fetch_scoreboard(self, url: str) -> list[dict]:
        resp = await self._client.get(url)
        resp.raise_for_status()
        data = resp.json()
        return data.get("events", [])

    def _parse_game(self, event: dict, sport: str, league: str) -> Optional[Game]:
        comps = event.get("competitions", [])
        if not comps:
            return None
        c = comps[0]
        competitors = c.get("competitors", [])
        home, away = self._extract_teams(competitors)
        if not home or not away:
            return None

        status = self._parse_comp_status(c)
        home_score = int(home.get("score", 0) or 0)
        away_score = int(away.get("score", 0) or 0)
        home_lines = self._extract_linescores(home)
        away_lines = self._extract_linescores(away)
        current_q = self._current_period(home_lines, away_lines, status)

        # 时间处理
        start_utc = event.get("date", "")
        start_bj = self._utc_to_beijing(start_utc)

        game = Game(
            id=str(event.get("id", "")),
            sport_type=sport,
            home_team=home.get("team", {}).get("displayName", ""),
            away_team=away.get("team", {}).get("displayName", ""),
            status=status,
            current_quarter=current_q,
            home_total=home_score,
            away_total=away_score,
            start_time=start_bj,
            league=league.upper() if sport == "basketball" else SOCCER_LEAGUES.get(league, league),
        )
        # 暂存 linescores 以便 get_game_detail 使用
        game._home_scores = home_lines  # type: ignore
        game._away_scores = away_lines  # type: ignore
        return game

    def _extract_teams(self, competitors: list) -> tuple[Optional[dict], Optional[dict]]:
        home = away = None
        for comp in competitors:
            ha = comp.get("homeAway", "")
            if ha == "home":
                home = comp
            elif ha == "away":
                away = comp
        if not home and not away and len(competitors) >= 2:
            home, away = competitors[0], competitors[1]
        return home, away

    def _extract_linescores(self, comp: dict) -> list[int]:
        """提取逐节/半场得分，篮球返回 [Q1, Q2, Q3, Q4, ...]，足球返回半场得分"""
        linescores = comp.get("linescores", [])
        if not linescores:
            return []
        return [int(ls.get("value", 0)) for ls in linescores]

    def _current_period(self, home_lines: list[int], away_lines: list[int], status: str) -> int:
        if status == "未开始":
            return 0
        if status == "已结束":
            return len(max(home_lines, away_lines, key=len)) if home_lines or away_lines else 0
        return len(home_lines) or len(away_lines)

    def _parse_event_status(self, event: dict) -> str:
        comps = event.get("competitions", [])
        if comps:
            return self._parse_comp_status(comps[0])
        return "未知"

    def _parse_comp_status(self, comp: dict) -> str:
        st = comp.get("status", {})
        name = st.get("type", {}).get("name", "")
        return STATUS_MAP.get(name, name)

    def _utc_to_beijing(self, utc_str: str) -> str:
        if not utc_str:
            return ""
        try:
            dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
            return dt.astimezone(CN_TZ).strftime("%H:%M")
        except (ValueError, AttributeError):
            return utc_str[-8:-3] if len(utc_str) >= 8 else utc_str
