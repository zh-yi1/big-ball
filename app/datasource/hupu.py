import re
import logging
import httpx
from typing import Optional
from bs4 import BeautifulSoup
from app.datasource.base import Game, GameDetail, DataSource

logger = logging.getLogger(__name__)


class HupuDataSource(DataSource):
    """虎扑体育数据源 — 解析网页 HTML 中的比赛数据"""

    NBA_GAMES = "https://nba.hupu.com/games"
    SOCCER_GAMES = "https://soccer.hupu.com/games"
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }

    def __init__(self):
        self._client = httpx.AsyncClient(timeout=15, headers=self.HEADERS, follow_redirects=True)

    async def get_live_games(self, sport_type: str) -> list[Game]:
        url = self.NBA_GAMES if sport_type == "basketball" else self.SOCCER_GAMES
        try:
            resp = await self._client.get(url)
            resp.raise_for_status()
            return self._parse_game_list(resp.text, sport_type)
        except Exception as e:
            logger.warning(f"Hupu fetch failed: {e}")
            return []

    async def get_game_detail(self, game_id: str, sport_type: str) -> Optional[GameDetail]:
        try:
            if game_id.startswith("http"):
                url = game_id
            else:
                url = f"{'https://nba.hupu.com' if sport_type == 'basketball' else 'https://soccer.hupu.com'}/games/{game_id}"
            resp = await self._client.get(url)
            if resp.status_code != 200:
                return None
            return self._parse_game_detail_html(resp.text, game_id, sport_type)
        except Exception as e:
            logger.warning(f"Hupu detail fetch failed: {e}")
            return None

    def _parse_game_list(self, html: str, sport_type: str) -> list[Game]:
        """从虎扑比赛中心页面解析比赛列表"""
        soup = BeautifulSoup(html, "html.parser")
        games = []

        # Hupu game items are typically in .table_list table or ul>li with match data
        # Each game row contains: team names, scores, status, link to detail page
        for item in soup.select("a[href*='/games/']"):
            href = item.get("href", "")
            text = item.get_text(" ", strip=True)
            # Filter out navigation links (dates, etc.)
            if not text or len(text) < 6:
                continue
            # Skip pure date entries like "05-11"
            if re.match(r'^\d{2}-\d{2}$', text.strip()):
                continue
            # Look for "team1 vs team2" pattern
            match = re.search(r'(.+?)\s*vs\s*(.+)', text)
            if not match:
                continue

            home = match.group(1).strip()
            away = match.group(2).strip()

            # Extract scores if present
            score_match = re.search(r'(\d+)\s*[-:：]\s*(\d+)', away)
            home_score, away_score = 0, 0
            if score_match:
                home_score = int(score_match.group(1))
                away_score = int(score_match.group(2))
                away = re.sub(r'\s*\d+\s*[-:：]\s*\d+.*', '', away).strip()

            status = self._parse_status_from_text(text)
            gid = str(abs(hash(f"hupu_{home}_{away}")) % (10 ** 10))

            games.append(Game(
                id=gid,
                sport_type=sport_type,
                home_team=home,
                away_team=away,
                status=status,
                current_quarter=self._infer_quarter(text, status),
                home_total=home_score,
                away_total=away_score,
            ))

        return games

    def _parse_game_detail_html(self, html: str, game_id: str, sport_type: str) -> Optional[GameDetail]:
        """从比赛详情页解析各节得分"""
        soup = BeautifulSoup(html, "html.parser")

        home_el = soup.select_one("[class*=home_team], [class*=team_home], .team-A")
        away_el = soup.select_one("[class*=away_team], [class*=team_away], .team-B")
        home = home_el.get_text(strip=True) if home_el else ""
        away = away_el.get_text(strip=True) if away_el else ""

        if not home:
            title = soup.select_one("title")
            if title:
                m = re.search(r'(.+?)\s*vs\s*(.+)', title.get_text(strip=True))
                if m:
                    home, away = m.group(1), m.group(2)

        home_scores = self._extract_quarter_cells(soup, "home") or self._extract_quarter_cells(soup, "A")
        away_scores = self._extract_quarter_cells(soup, "away") or self._extract_quarter_cells(soup, "B")

        home_total = sum(home_scores) if home_scores else 0
        away_total = sum(away_scores) if away_scores else 0

        return GameDetail(
            id=game_id,
            sport_type=sport_type,
            home_team=home,
            away_team=away,
            status="进行中" if home_scores else "未知",
            current_quarter=len(home_scores),
            home_total=home_total,
            away_total=away_total,
            home_scores=home_scores,
            away_scores=away_scores,
            raw_data={},
        )

    def _extract_quarter_cells(self, soup, cls_hint: str) -> list[int]:
        """从 HTML 表格中提取各节得分列"""
        scores = []
        for cell in soup.select(f"[class*={cls_hint}] [class*=score], [class*={cls_hint}] td"):
            text = cell.get_text(strip=True)
            if text.isdigit():
                scores.append(int(text))
            if len(scores) >= 4:
                break
        return scores if len(scores) >= 1 else []

    def _parse_status_from_text(self, text: str) -> str:
        if any(w in text for w in ["已结束", "完赛", "FT", "完场"]):
            return "已结束"
        if any(w in text for w in ["未开始", "未赛", "预告"]):
            return "未开始"
        if any(w in text for w in ["中场", "HT", "半场"]):
            return "中场休息"
        if any(w in text for w in ["取消", "延期", "腰斩"]):
            return "已取消"
        return "进行中"

    def _infer_quarter(self, text: str, status: str) -> int:
        if status in ("未开始", "已结束", "已取消"):
            return 0
        m = re.search(r'[Qq]\s*(\d)|第\s*(\d)\s*节', text)
        if m:
            return int(m.group(1) or m.group(2))
        return 1
