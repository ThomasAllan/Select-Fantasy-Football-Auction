from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from select_football.common.models import ManagerStanding

_TEMPLATE_DIR = Path(__file__).parent / "templates"


def render_report(
    standings: list[ManagerStanding],
    timestamp: datetime | None = None,
    *,
    season_id: str | None = None,
    gameweek: int | None = None,
    movement: dict[str, int | None] | None = None,
) -> str:
    """Render the monthly league-table email.

    movement maps manager_name -> places gained since the previous monthly email
    (+ve = moved up, 0 = unchanged, None = not in that snapshot / new). Pass None
    to omit the movement column entirely (e.g. the first ever email).
    """
    env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=True)
    template = env.get_template("monthly_report.html.j2")

    ts = timestamp or datetime.now()
    formatted = ts.strftime("%d %b %Y, %H:%M")

    return template.render(
        standings=standings,
        timestamp=formatted,
        season_id=season_id,
        gameweek=gameweek,
        movement=movement,
    )
