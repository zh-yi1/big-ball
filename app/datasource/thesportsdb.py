import re
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
import httpx
from app.datasource.base import Game, GameDetail, DataSource

logger = logging.getLogger(__name__)
CN_TZ = timezone(timedelta(hours=8))

API_BASE = "https://www.thesportsdb.com/api/v1/json"

STATUS_MAP = {
    "Not Started": "未开始", "NS": "未开始",
    "1H": "上半场", "HT": "中场休息", "2H": "下半场",
    "FT": "已结束", "Match Finished": "已结束",
    "AET": "加时结束", "AOT": "加时结束",
    "Postponed": "延期", "Cancelled": "已取消",
}


class TheSportsDBDataSource(DataSource):
    """TheSportsDB 数据源 — strResult 含逐节得分"""

    def __init__(self, api_key: str = ""):
        self._key = api_key or _load_key()
        self._client = httpx.AsyncClient(timeout=15)

    async def _get(self, path: str) -> dict:
        url = f"{API_BASE}/{self._key}/{path}"
        resp = await self._client.get(url)
        resp.raise_for_status()
        return resp.json()

    async def get_today_games(self, force: bool = False) -> dict[str, list[Game]]:
        today = datetime.now(CN_TZ).strftime("%Y-%m-%d")
        bb, fb = [], []

        try:
            data = await self._get(f"eventsday.php?d={today}&s=Basketball")
            for e in data.get("events", []):
                g = self._parse_event(e, "basketball")
                if g:
                    bb.append(g)
        except Exception as exc:
            logger.warning(f"TheSportsDB basketball failed: {exc}")

        try:
            data = await self._get(f"eventsday.php?d={today}&s=Soccer")
            for e in data.get("events", []):
                g = self._parse_event(e, "football")
                if g:
                    fb.append(g)
        except Exception as exc:
            logger.warning(f"TheSportsDB football failed: {exc}")

        return {"basketball": bb, "football": fb}

    async def get_live_games(self, sport_type: str) -> list[Game]:
        sport = "Basketball" if sport_type == "basketball" else "Soccer"
        try:
            data = await self._get(f"latestscores.php?s={sport}")
            games = []
            for e in data.get("livescore", data.get("events", [])):
                g = self._parse_event(e, sport_type)
                if g and g.status not in ("未开始", "已结束", "延期", "已取消"):
                    games.append(g)
            return games
        except Exception:
            return []

    async def get_game_detail(self, game_id: str, sport_type: str) -> Optional[GameDetail]:
        try:
            data = await self._get(f"lookupevent.php?id={game_id}")
            for e in data.get("events", []):
                return self._parse_detail(e, sport_type)
        except Exception:
            pass
        return None

    def _parse_event(self, e: dict, sport: str) -> Optional[Game]:
        home = e.get("strHomeTeam", "")
        away = e.get("strAwayTeam", "")
        if not home or not away:
            return None
        home_q, away_q = _parse_quarters(e.get("strResult", ""))
        status = STATUS_MAP.get(e.get("strStatus", ""), e.get("strStatus", "未开始"))
        home_total = int(e.get("intHomeScore") or sum(home_q) or 0)
        away_total = int(e.get("intAwayScore") or sum(away_q) or 0)

        g = Game(
            id=str(e.get("idEvent", "")),
            sport_type=sport,
            home_team=home,
            away_team=away,
            status=status,
            current_quarter=_infer_quarter(home_q, status),
            home_total=home_total,
            away_total=away_total,
            start_time=_fmt_utc(e.get("strTimestamp", "")),
            league=e.get("strLeague", ""),
        )
        g._home_scores = home_q  # type: ignore
        g._away_scores = away_q  # type: ignore
        g._raw_data = e  # type: ignore
        return g

    def _parse_detail(self, e: dict, sport: str) -> Optional[GameDetail]:
        g = self._parse_event(e, sport)
        if not g:
            return None
        return GameDetail(
            id=g.id, sport_type=g.sport_type,
            home_team=g.home_team, away_team=g.away_team,
            status=g.status, current_quarter=g.current_quarter,
            home_total=g.home_total, away_total=g.away_total,
            home_scores=getattr(g, "_home_scores", []),
            away_scores=getattr(g, "_away_scores", []),
            raw_data=getattr(g, "_raw_data", {}),
        )


def _parse_quarters(result: str) -> tuple[list[int], list[int]]:
    """解析 strResult: 'Team Quarters:<br>16 29 25 25 <br><br>Team2 Quarters:<br>9 22 22 23'"""
    if not result:
        return [], []
    # Remove HTML tags
    text = result.replace("<br>", "\n").replace("<br/>", "\n")
    # Find home and away quarter lines
    lines = text.split("\n")
    home_q, away_q = [], []
    current = None
    for line in lines:
        line = line.strip()
        if "Quarters:" in line:
            current = "home" if not home_q else "away"
            continue
        if current and re.match(r'^\d+(\s+\d+)*$', line):
            nums = [int(x) for x in line.split()]
            if current == "home" and not home_q:
                home_q = nums
                current = None
            elif current == "away" and not away_q:
                away_q = nums
                current = None
    return home_q, away_q


def _infer_quarter(home_q: list[int], status: str) -> int:
    if status == "未开始":
        return 0
    if status in ("已结束", "加时结束"):
        return len(home_q)
    return len(home_q) or 1


def _fmt_utc(ts: str) -> str:
    """UTC → 北京时间 HH:MM"""
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.astimezone(CN_TZ).strftime("%H:%M")
    except (ValueError, AttributeError):
        return ts[-8:-3] if len(ts) >= 8 else ts


def _load_key() -> str:
    from app.config import load_config
    return load_config().get("thesportsdb", {}).get("key", "3")
