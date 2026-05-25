import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
import httpx
from app.config import load_config
from app.datasource.base import Game, GameDetail, DataSource

logger = logging.getLogger(__name__)

CN_TZ = timezone(timedelta(hours=8))
API_BASE = "http://api.isportsapi.com"

# status 码映射
STATUS_MAP = {
    0: "未开始", 1: "第1节", 2: "第2节", 3: "第3节", 4: "第4节",
    50: "中场休息", -1: "已结束", -2: "待定", -3: "中断",
    -4: "已取消", -5: "延期",
    5: "加时1", 6: "加时2", 7: "加时3",
}


class ISportsDataSource(DataSource):
    """iSports API 数据源 — 800+ 篮球联赛，逐节得分独立字段"""

    LIVE_STATUSES = {"第1节", "第2节", "第3节", "第4节",
                     "加时1", "加时2", "加时3", "中场休息"}

    def __init__(self):
        cfg = load_config().get("isports", {})
        self._api_key = cfg.get("key", "")
        self._api_url = cfg.get("api_url", API_BASE)
        self._client: Optional[httpx.AsyncClient] = None
        self._today_cache: Optional[dict] = None
        self._cache_time: Optional[float] = None
        self._cache_ttl = 300

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=15)
        return self._client

    async def _get(self, path: str, params: dict = None) -> dict:
        params = params or {}
        params["api_key"] = self._api_key
        client = self._get_client()
        resp = await client.get(f"{self._api_url}{path}", params=params)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            msg = data.get("message", "unknown error")
            raise RuntimeError(f"iSports API error: {msg}")
        return data

    # ── 今日赛程 ──────────────────────────────

    async def get_today_games(self, force: bool = False) -> dict[str, list[Game]]:
        import time
        now = time.monotonic()
        if not force and self._today_cache is not None and self._cache_time is not None:
            if now - self._cache_time < self._cache_ttl:
                return self._today_cache

        today = datetime.now(CN_TZ).strftime("%Y-%m-%d")
        games = []
        try:
            data = await self._get("/sport/basketball/livescores", {"date": today})
            for m in data.get("data", []):
                g = self._parse_livescore(m)
                if g:
                    games.append(g)
        except Exception as e:
            logger.warning(f"iSports livescores failed: {e}")
        # 按比赛时间(北京时间)排序
        games.sort(key=lambda g: g.start_time)
        result = {"basketball": games, "football": []}
        self._today_cache = result
        self._cache_time = time.monotonic()
        return result

    async def get_live_games(self, sport_type: str) -> list[Game]:
        if sport_type != "basketball":
            return []
        games = []
        for g in (await self.get_today_games()).get("basketball", []):
            if g.status in self.LIVE_STATUSES:
                games.append(g)
        return games

    async def get_game_detail(self, game_id: str, sport_type: str) -> Optional[GameDetail]:
        """从缓存读取，避免重复 API 调用"""
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

    # ── 解析 ──────────────────────────────────

    def _parse_livescore(self, m: dict) -> Optional[Game]:
        home = m.get("homeName", "")
        away = m.get("awayName", "")
        if not home or not away:
            return None

        status_code = m.get("status", 0)
        status = STATUS_MAP.get(status_code, f"状态{status_code}")

        home_q = self._extract_quarters(m, "home")
        away_q = self._extract_quarters(m, "away")

        current_q = 0
        if 1 <= status_code <= 4:
            current_q = status_code
        elif status_code in (5, 6, 7):
            current_q = 4 + (status_code - 4)
        elif status_code == -1:
            current_q = len(home_q)

        g = Game(
            id=str(m.get("matchId", "")),
            sport_type="basketball",
            home_team=home,
            away_team=away,
            status=status,
            current_quarter=current_q,
            home_total=int(m.get("homeScore") or 0),
            away_total=int(m.get("awayScore") or 0),
            start_time=self._fmt_time(m.get("matchTime", 0)),
            league=m.get("leagueName", ""),
        )
        g._home_scores = home_q  # type: ignore
        g._away_scores = away_q  # type: ignore
        g._raw_data = m  # type: ignore
        return g

    def _extract_quarters(self, m: dict, side: str) -> list[int]:
        """从独立字段提取逐节得分 [Q1, Q2, Q3, Q4, ...OT]"""
        pfx = f"{side}"
        qs = []
        for key in ("FirstQuarterScore", "SecondQuarterScore",
                     "ThirdQuarterScore", "FourthQuarterScore"):
            v = m.get(f"{pfx}{key}")
            qs.append(int(v) if v is not None else 0)
        # 加时
        for ot_key in ("FirstOverTimeScore", "SecondOverTimeScore", "ThirdOverTimeScore"):
            v = m.get(f"{pfx}{ot_key}")
            if v is not None:
                qs.append(int(v))
        return qs

    def _fmt_time(self, ts: int) -> str:
        """Unix 时间戳 → 北京时间 HH:MM"""
        if not ts:
            return ""
        try:
            dt = datetime.fromtimestamp(ts, tz=CN_TZ)
            return dt.strftime("%H:%M")
        except (ValueError, OSError):
            return ""
