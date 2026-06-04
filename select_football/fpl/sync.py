"""Orchestrates pulling FPL data and writing it to the CSV store."""
from datetime import date

import pandas as pd

from select_football.common.csv_store import CsvStore
from select_football.common.logging import get_logger
from select_football.fpl.client import FplClient
from select_football.fpl.models import BootstrapData, FplElement

log = get_logger(__name__)

_ELEMENT_TYPE_MAP = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
_STATUS_MAP = {"a": "A", "i": "I", "d": "D", "s": "S", "u": "U"}


def _player_code(season: str, type_: str, element_id: int) -> str:
    return f"{season}-{type_}-{element_id}"


def sync_players(
    bootstrap: BootstrapData,
    season: str,
    store: CsvStore,
    dry_run: bool = False,
) -> None:
    """Sync teams into teams.csv and players into players.csv for the given season."""
    if store.is_season_closed(season):
        log.info("season_closed_skip_players", season=season)
        return

    team_rows = []
    player_rows = []

    for team in bootstrap.teams:
        team_rows.append({
            "code": _player_code(season, "team", team.id),
            "season": season,
            "type": "team",
            "element_id": team.id,
            "full_name": team.name,
            "friendly_name": team.name,
            "fpl_position": "",
            "team_id": team.id,
            "team_code": str(team.code),
            "status": "",
            "news": "",
            "news_date": "",
            "photo_url": f"https://resources.premierleague.com/premierleague/badges/t{team.code}.png",
        })

    for player in bootstrap.elements:
        news_date = ""
        if player.news_added:
            try:
                news_date = player.news_added[:10]
            except Exception:
                pass

        player_rows.append({
            "code": _player_code(season, "player", player.id),
            "season": season,
            "type": "player",
            "element_id": player.id,
            "fpl_permanent_code": str(player.code),
            "full_name": f"{player.first_name} {player.second_name}",
            "friendly_name": player.web_name,
            "fpl_position": _ELEMENT_TYPE_MAP.get(player.element_type, ""),
            "team_id": player.team,
            "team_code": str(player.team_code),
            "status": _STATUS_MAP.get(player.status, player.status.upper()),
            "news": player.news,
            "news_date": news_date,
            "photo_url": f"https://resources.premierleague.com/premierleague25/photos/players/110x140/{player.photo.replace('.jpg', '.png')}",
        })

    all_rows = team_rows + player_rows
    log.info("sync_players", teams=len(team_rows), players=len(player_rows), season=season, dry_run=dry_run)
    if not dry_run:
        store.upsert("players", pd.DataFrame(all_rows), key_cols=["code"])


def sync_outfield_goals(
    client: FplClient,
    element: FplElement,
    season: str,
    store: CsvStore,
    dry_run: bool = False,
) -> None:
    """Fetch per-GW goal stats for a single outfield player and upsert into goals.csv."""
    if store.is_season_closed(season):
        return
    history = client.get_element_history(element.id)
    if not history:
        return

    rows = [
        {
            "player_code": _player_code(season, "player", element.id),
            "season_id": season,
            "game_week": h.round,
            "goals_scored": h.goals_scored,
            "goals_conceded": 0,
        }
        for h in history
        if h.goals_scored > 0
    ]
    if not rows:
        return

    df = pd.DataFrame(rows)
    log.info("sync_outfield_goals", player_id=element.id, gws=len(df), dry_run=dry_run)
    if not dry_run:
        store.upsert("goals", df, key_cols=["player_code", "season_id", "game_week"])


def sync_team_goals_conceded(
    client: FplClient,
    team_id: int,
    season: str,
    store: CsvStore,
    dry_run: bool = False,
) -> None:
    """Fetch goals conceded per GW for a GK team and upsert into goals.csv."""
    if store.is_season_closed(season):
        return
    fixtures = client.get_team_fixtures(team_id)
    rows = []

    conceded_by_gw: dict[int, int] = {}
    for fix in fixtures:
        if not fix.finished:
            continue
        if fix.event == 0:
            continue
        conceded = fix.team_a_score if fix.team_h == team_id else fix.team_h_score
        conceded_by_gw[fix.event] = conceded_by_gw.get(fix.event, 0) + (conceded or 0)

    rows = [
        {
            "player_code": _player_code(season, "team", team_id),
            "season_id": season,
            "game_week": gw,
            "goals_scored": 0,
            "goals_conceded": total,
        }
        for gw, total in conceded_by_gw.items()
        if total > 0
    ]

    if not rows:
        return

    df = pd.DataFrame(rows)
    log.info("sync_team_goals_conceded", team_id=team_id, gws=len(df), dry_run=dry_run)
    if not dry_run:
        store.upsert("goals", df, key_cols=["player_code", "season_id", "game_week"])


def sync_gk_player_goals(
    client: FplClient,
    gk_elements: list[FplElement],
    season: str,
    store: CsvStore,
    dry_run: bool = False,
) -> None:
    """Fetch goal stats for all GK players (for the +4 bonus) and upsert into goals.csv."""
    if store.is_season_closed(season):
        return
    for gk in gk_elements:
        history = client.get_element_history(gk.id)
        scored_gws = [h for h in history if h.goals_scored > 0]
        if not scored_gws:
            continue

        rows = [
            {
                "player_code": _player_code(season, "player", gk.id),
                "season_id": season,
                "game_week": h.round,
                "goals_scored": h.goals_scored,
                "goals_conceded": 0,
            }
            for h in scored_gws
        ]
        df = pd.DataFrame(rows)
        log.info("sync_gk_player_goals", player_id=gk.id, scored_gws=len(df), dry_run=dry_run)
        if not dry_run:
            store.upsert("goals", df, key_cols=["player_code", "season_id", "game_week"])


def last_synced_gw(bootstrap: BootstrapData) -> int:
    """Return the highest GW we have data for — finished or currently in progress."""
    relevant = [e.id for e in bootstrap.events if e.finished or e.is_current]
    return max(relevant) if relevant else 0


def is_gw_in_progress(bootstrap: BootstrapData) -> bool:
    """Return True if a game week is currently active and not yet data-checked."""
    for event in bootstrap.events:
        if event.is_current and not event.finished and not event.data_checked:
            return True
    return False


def is_season_in_progress(bootstrap: BootstrapData) -> bool:
    """Return True if at least GW1 has finished (i.e. there is data to sync)."""
    events = bootstrap.events
    if not events:
        return False
    return events[0].finished
