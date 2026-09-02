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


def render_report_text(
    standings: list[ManagerStanding],
    *,
    season_id: str | None = None,
    gameweek: int | None = None,
) -> str:
    """Plain-text version of the monthly table.

    Sent as the text/plain alternative alongside the HTML — a message with only
    an HTML part is a strong spam signal, and some clients junk it outright.
    """
    title = "Select Fantasy Football — Monthly League Table"
    if season_id:
        title += f" ({season_id})"
    lines = [title]
    if gameweek is not None:
        lines.append(f"After Gameweek {gameweek}")
    lines.append("")
    for s in standings:
        pts = f"{s.total_points:g}"
        row = f"{s.position:>2}. {s.manager_name:<22} {pts:>5} pts"
        if s.prize:
            row += f"  (£{s.prize:g})"
        lines.append(row)
    lines.append("")
    return "\n".join(lines)
