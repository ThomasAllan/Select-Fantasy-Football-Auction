from dataclasses import dataclass, field


@dataclass
class FplEvent:
    id: int
    name: str
    finished: bool
    is_current: bool
    data_checked: bool


@dataclass
class FplTeam:
    id: int
    code: int
    name: str
    short_name: str


@dataclass
class FplElement:
    """A single player from the bootstrap-static elements list."""
    id: int
    code: int
    first_name: str
    second_name: str
    web_name: str
    element_type: int  # 1=GKP, 2=DEF, 3=MID, 4=FWD
    team: int          # team id
    team_code: int
    status: str        # a / i / d / s / u
    news: str
    news_added: str    # ISO datetime string or empty
    photo: str         # filename e.g. "123456.jpg"


@dataclass
class FplElementHistory:
    """Per-gameweek history entry from element-summary."""
    element: int
    fixture: int
    round: int         # game week number
    goals_scored: int
    goals_conceded: int
    minutes: int


@dataclass
class FplFixture:
    id: int
    event: int         # game week (0 if not assigned)
    finished: bool
    team_h: int
    team_a: int
    team_h_score: int | None
    team_a_score: int | None


@dataclass
class BootstrapData:
    events: list[FplEvent] = field(default_factory=list)
    teams: list[FplTeam] = field(default_factory=list)
    elements: list[FplElement] = field(default_factory=list)
