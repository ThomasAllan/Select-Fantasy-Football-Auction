from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from select_football.common.models import ManagerStanding

_TEMPLATE_DIR = Path(__file__).parent / "templates"


def render_report(
    standings: list[ManagerStanding],
    timestamp: datetime | None = None,
) -> str:
    env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=True)
    template = env.get_template("monthly_report.html.j2")

    ts = timestamp or datetime.now()
    formatted = ts.strftime("%d-%b-%Y %H:%M:%S")

    return template.render(standings=standings, timestamp=formatted)
