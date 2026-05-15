import json
import logging
import httpx
from typing import Optional
from app.datasource.base import Game, GameDetail, DataSource

logger = logging.getLogger(__name__)


class LeisuDataSource(DataSource):
    """雷速体育数据源 — 优先使用内嵌 API，失败则降级到 Playwright

    雷速体育的反爬较严格：请求签名 + zlib 压缩 + 凯撒密码混淆。
    此实现尝试通过公开 API 接口获取数据，如失败则返回空列表，
    需要 Playwright 的版本记为 TODO。
    """

    BASE_URL = "https://live.leisu.com"
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": "https://live.leisu.com/",
    }

    def __init__(self):
        self._client = httpx.AsyncClient(timeout=15, headers=self.HEADERS, follow_redirects=True)

    async def get_live_games(self, sport_type: str) -> list[Game]:
        """获取进行中的比赛列表"""
        # Try API endpoint first
        games = await self._try_api_games(sport_type)
        if games:
            return games
        # Fallback: return empty (Playwright support is TODO)
        logger.info("Leisu API returned no data, returning empty game list")
        return []

    async def get_game_detail(self, game_id: str, sport_type: str) -> Optional[GameDetail]:
        return None  # Leisu detail requires Playwright

    async def _try_api_games(self, sport_type: str) -> list[Game]:
        """尝试通过雷速的公开数据接口获取比赛"""
        # Leisu has a legacy JSONP-style API at /app/match/live
        # It's heavily protected but we try common patterns
        endpoints = [
            f"{self.BASE_URL}/app/match/live",
            f"{self.BASE_URL}/app/match/live?type={sport_type}",
        ]
        for url in endpoints:
            try:
                resp = await self._client.get(url)
                if resp.status_code == 200 and len(resp.text) > 1000:
                    return self._parse_response(resp.text, sport_type)
            except Exception:
                continue
        return []

    def _parse_response(self, text: str, sport_type: str) -> list[Game]:
        """尝试解析响应数据"""
        games = []
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                matches = data.get("data", data.get("matches", []))
                for m in matches:
                    game = self._parse_game(m, sport_type)
                    if game:
                        games.append(game)
        except json.JSONDecodeError:
            pass
        return games

    def _parse_game(self, m: dict, sport_type: str) -> Optional[Game]:
        home = m.get("home_name", m.get("home", ""))
        away = m.get("away_name", m.get("away", ""))
        if not home or not away:
            return None
        gid = str(m.get("id", m.get("match_id", "")))
        return Game(
            id=gid,
            sport_type=sport_type,
            home_team=home,
            away_team=away,
            status=str(m.get("status", m.get("state", "进行中"))),
            current_quarter=int(m.get("quarter", m.get("period", 0))),
            home_total=int(m.get("home_score", m.get("score_home", 0) or 0)),
            away_total=int(m.get("away_score", m.get("score_away", 0) or 0)),
        )
