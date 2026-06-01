from datetime import date
from typing import Optional

from pydantic import BaseModel


class Season(BaseModel):
    season_id: str
    start_date: date
    end_date: date
    last_gw_synced: Optional[int] = None


class PositionModifier(BaseModel):
    position: str  # DEF / MID / FWD / GK
    multiplier: float


class Prize(BaseModel):
    season_id: str
    position: int
    prize_amount: float


class Manager(BaseModel):
    name: str  # primary key
    email: str


class Override(BaseModel):
    player_code: str
    season_id: str
    game_week: int
    override_points: float
    reason: str = ""


class GoalRecord(BaseModel):
    player_code: str
    season_id: str
    game_week: int
    goals_scored: int = 0
    goals_conceded: int = 0


class ManagerSelection(BaseModel):
    player_code: str
    season_id: str
    manager_name: str  # FK to managers.name
    position: str  # DEF / MID / FWD / GK
    cost: float
    gw_from: int
    gw_to: Optional[int] = None  # None = still active


class Player(BaseModel):
    code: str
    season: str
    type: str  # player / team
    element_id: int
    full_name: str
    friendly_name: str
    fpl_position: str  # GKP / DEF / MID / FWD (or empty for teams)
    team_id: int
    team_code: str = ""
    status: str = ""
    news: str = ""
    news_date: Optional[date] = None
    photo_url: str = ""


class ManagerStanding(BaseModel):
    position: int
    manager_name: str
    total_points: float
    prize: Optional[float] = None
    gw_breakdown: dict[int, float] = {}
