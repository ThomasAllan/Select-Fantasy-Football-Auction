import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from select_football.fpl.models import (
    BootstrapData,
    FplElement,
    FplElementHistory,
    FplEvent,
    FplFixture,
    FplTeam,
)

_ELEMENT_TYPE_MAP = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}


class FplClient:
    def __init__(self, base_url: str = "https://fantasy.premierleague.com/api") -> None:
        self._base = base_url.rstrip("/")
        self._client = httpx.Client(timeout=30)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "FplClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def _get(self, path: str) -> dict:
        response = self._client.get(f"{self._base}/{path.lstrip('/')}")
        response.raise_for_status()
        return response.json()  # type: ignore[no-any-return]

    def get_bootstrap(self) -> BootstrapData:
        data = self._get("bootstrap-static/")

        events = [
            FplEvent(
                id=e["id"],
                name=e["name"],
                finished=e["finished"],
                is_current=e["is_current"],
                data_checked=e["data_checked"],
            )
            for e in data["events"]
        ]

        teams = [
            FplTeam(
                id=t["id"],
                code=t["code"],
                name=t["name"],
                short_name=t["short_name"],
            )
            for t in data["teams"]
        ]

        elements = [
            FplElement(
                id=p["id"],
                code=p["code"],
                first_name=p["first_name"],
                second_name=p["second_name"],
                web_name=p["web_name"],
                element_type=p["element_type"],
                team=p["team"],
                team_code=p["team_code"],
                status=p["status"],
                news=p.get("news", ""),
                news_added=p.get("news_added") or "",
                photo=p.get("photo", ""),
            )
            for p in data["elements"]
        ]

        return BootstrapData(events=events, teams=teams, elements=elements)

    def get_element_history(self, element_id: int) -> list[FplElementHistory]:
        data = self._get(f"element-summary/{element_id}/")
        return [
            FplElementHistory(
                element=h["element"],
                fixture=h["fixture"],
                round=h["round"],
                goals_scored=h["goals_scored"],
                goals_conceded=h["goals_conceded"],
                minutes=h["minutes"],
            )
            for h in data.get("history", [])
        ]

    def get_team_fixtures(self, team_id: int) -> list[FplFixture]:
        data = self._get(f"fixtures/?team={team_id}")
        return [
            FplFixture(
                id=f["id"],
                event=f.get("event") or 0,
                finished=f["finished"],
                team_h=f["team_h"],
                team_a=f["team_a"],
                team_h_score=f.get("team_h_score"),
                team_a_score=f.get("team_a_score"),
            )
            for f in data
        ]
