from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Game:
    id: str
    sport_type: str  # basketball / football
    home_team: str
    away_team: str
    status: str  # 未开始 / 进行中 / 中场休息 / 已结束
    current_quarter: int  # 篮球: 1-4, 足球: 1=上半场 2=下半场
    home_total: int = 0
    away_total: int = 0
    start_time: str = ""      # 比赛开始时间
    league: str = ""          # 联赛名称


@dataclass
class GameDetail(Game):
    home_scores: list[int] = field(default_factory=list)
    away_scores: list[int] = field(default_factory=list)
    raw_data: dict = field(default_factory=dict)


@dataclass
class TodayGames:
    date: str
    games: list[Game]


class DataSource(ABC):
    @abstractmethod
    async def get_live_games(self, sport_type: str) -> list[Game]:
        pass

    @abstractmethod
    async def get_game_detail(self, game_id: str, sport_type: str) -> Optional[GameDetail]:
        pass

    async def get_today_games(self, force: bool = False) -> dict[str, list[Game]]:
        """返回今日比赛，key 为 sport_type，value 为比赛列表。
        默认返回空，子类可 override。
        """
        return {}
