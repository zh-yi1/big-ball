import logging
import asyncio
import time
from datetime import datetime, timezone, timedelta
from typing import Optional
import httpx
from app.datasource.base import Game, GameDetail, DataSource

logger = logging.getLogger(__name__)

CN_TZ = timezone(timedelta(hours=8))

# 懂球帝 tab API — 按运动类型返回完整赛程
TAB_API = "https://api.dongqiudi.com/data/tab/new/{sport}"

# 实时比赛 API
LIVE_API = "https://api.dongqiudi.com/v2/match/live"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0",
    "Accept": "application/json",
    "Referer": "https://www.dongqiudi.com/live",
}

STATUS_MAP = {
    "Fixture": "未开始", "Playing": "进行中", "HT": "中场休息",
    "Played": "已结束", "Finished": "已结束", "Postponed": "延期",
    "Canceled": "已取消", "TBD": "待定", "Uncertain": "待定",
}


class DongqiudiDataSource(DataSource):
    """懂球帝数据源 — 使用公开 tab API，无需鉴权"""

    def __init__(self):
        self._client = httpx.AsyncClient(timeout=15)
        self._today_cache: Optional[dict] = None
        self._cache_time: Optional[float] = None
        self._cache_ttl = 300  # 5 分钟缓存

    # ── 实时比赛（进行中）─────────────────────────────

    async def get_live_games(self, sport_type: str) -> list[Game]:
        matches = await self._fetch_live_api()
        games = []
        for m in matches:
            if self._sport_type(m) != sport_type:
                continue
            games.append(self._build_game(m, sport_type))
        return games

    async def get_game_detail(self, game_id: str, sport_type: str) -> Optional[GameDetail]:
        matches = await self._fetch_live_api()
        for m in matches:
            if str(m.get("match_id", "")) == game_id:
                return self._build_detail(m, sport_type)
        return None

    async def _fetch_live_api(self) -> list[dict]:
        try:
            resp = await self._client.get(LIVE_API, headers=HEADERS)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return data.get("data", data.get("matches", []))
            return []
        except Exception as e:
            logger.warning(f"Dongqiudi live API failed: {e}")
            return []

    # ── 今日赛程（全部比赛）─────────────────────────────

    # 非真实比赛的联赛名（专家分析、爆料等内容）
    FAKE_LEAGUES = {"主客状态", "足球之路", "豪门档案", "墨超爆料", "爆料情报"}

    async def get_today_games(self, force: bool = False) -> dict[str, list[Game]]:
        """返回今日比赛 {basketball: [...], football: [...]}"""
        now = time.monotonic()
        if not force and self._today_cache is not None and self._cache_time is not None:
            if now - self._cache_time < self._cache_ttl:
                return self._today_cache

        today_str = datetime.now(CN_TZ).strftime("%Y-%m-%d")

        try:
            all_matches = await self._fetch_tab("lottery")
        except Exception as e:
            logger.warning(f"Dongqiudi lottery tab failed: {e}")
            return {"basketball": [], "football": []}

        basketball, football = [], []
        for m in all_matches:
            if not self._is_today(m, today_str):
                continue
            league = m.get("competition_name", "")
            if league in self.FAKE_LEAGUES:
                continue
            ct = m.get("cmp_type", "")
            if ct == "basketball":
                sport = "basketball"
            elif ct == "soccer":
                sport = "football"
            else:
                continue
            game = self._build_game(m, sport)
            game.start_time = self._to_beijing_time(m.get("start_play", ""))
            game.league = league
            if m.get("status") == "Fixture":
                game.status = "未开始"
            if sport == "basketball":
                basketball.append(game)
            else:
                football.append(game)

        result = {"basketball": basketball, "football": football}
        self._today_cache = result
        self._cache_time = now
        return result

    async def _fetch_tab(self, sport: str) -> list[dict]:
        """调用懂球帝 tab API，sport=basketball/soccer/lottery"""
        today = datetime.now(CN_TZ).strftime("%Y-%m-%d")
        url = f"{TAB_API.format(sport=sport)}?start={today}160000&init=1&platform=www"
        resp = await self._client.get(url, headers=HEADERS)
        resp.raise_for_status()
        data = resp.json()
        return data.get("list", [])

    # ── 辅助方法 ─────────────────────────────────

    def _is_today(self, m: dict, today: str) -> bool:
        for key in ("date_utc", "start_play"):
            val = m.get(key, "")
            if val and str(val)[:10] == today:
                return True
        return False

    def _sport_type(self, m: dict) -> str:
        ct = m.get("cmp_type", "").lower()
        if ct == "basketball":
            return "basketball"
        if ct == "soccer":
            return "football"
        # fallback: check competition name
        league = (m.get("competition_name", "") or "").lower()
        for kw in ("nba", "cba", "wnba", "basketball", "篮球"):
            if kw in league:
                return "basketball"
        return "football"

    def _to_beijing_time(self, raw: str) -> str:
        """UTC → 北京时间 HH:MM"""
        if not raw:
            return ""
        try:
            dt = datetime.strptime(raw.strip(), "%Y-%m-%d %H:%M:%S")
            dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(CN_TZ).strftime("%H:%M")
        except ValueError:
            return raw[-8:-3] if len(raw) >= 8 else raw

    def _build_game(self, m: dict, sport: str) -> Game:
        return Game(
            id=str(m.get("match_id", "")),
            sport_type=sport,
            home_team=m.get("team_A_name", ""),
            away_team=m.get("team_B_name", ""),
            status=self._parse_status(m),
            current_quarter=self._parse_quarter(m),
            home_total=int(m.get("fs_A", 0) or 0),
            away_total=int(m.get("fs_B", 0) or 0),
        )

    def _build_detail(self, m: dict, sport: str) -> GameDetail:
        hq = self._extract_quarter_scores(m, "A")
        aq = self._extract_quarter_scores(m, "B")
        return GameDetail(
            id=str(m.get("match_id", "")),
            sport_type=sport,
            home_team=m.get("team_A_name", ""),
            away_team=m.get("team_B_name", ""),
            status=self._parse_status(m),
            current_quarter=self._parse_quarter(m),
            home_total=int(m.get("fs_A", 0) or 0),
            away_total=int(m.get("fs_B", 0) or 0),
            home_scores=hq,
            away_scores=aq,
            raw_data=m,
        )

    def _extract_quarter_scores(self, m: dict, side: str) -> list[int]:
        for key in (f"quarter_scores_{side}", f"qs_{side}", f"period_scores_{side}"):
            if key in m and isinstance(m[key], list):
                return [int(s) for s in m[key]]
        # 篮球可能有 hts (半场得分)
        hts = m.get(f"hts_{side}", 0)
        if hts:
            return [int(hts)]
        return []

    def _parse_status(self, m: dict) -> str:
        s = m.get("status", m.get("match_status", "Fixture"))
        if isinstance(s, str):
            return STATUS_MAP.get(s, s)
        int_map = {1: "未开始", 2: "上半场", 3: "中场休息",
                   4: "下半场", 5: "加时", 0: "已结束", -1: "已结束"}
        return int_map.get(int(s), "进行中")

    def _parse_quarter(self, m: dict) -> int:
        s = m.get("status", 1)
        if isinstance(s, int):
            return {1: 0, 2: 1, 3: 2, 4: 2, 5: 3, 0: 0, -1: 0}.get(s, 0)
        if s in ("Fixture", "Played", "Finished"):
            return 0
        return 1
