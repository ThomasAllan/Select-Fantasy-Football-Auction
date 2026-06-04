"""Job: pull current FPL data and update CSV files.

Usage:
    uv run sync-scores
    uv run sync-scores --dry-run
"""
import pandas as pd
import click

from select_football.common.csv_store import CsvStore
from select_football.common.logging import configure_logging, get_logger
from select_football.common.models import Prize
from select_football.config import get_settings
from select_football.core.scoring import score_gk_team, score_outfield_player
from select_football.core.standings import (
    _goals_for,
    _gk_player_codes_for_team,
    _int,
    compute_standings,
)
from select_football.fpl.client import FplClient
from select_football.fpl.sync import (
    is_gw_in_progress,
    is_season_in_progress,
    last_synced_gw,
    sync_gk_player_goals,
    sync_outfield_goals,
    sync_players,
    sync_team_goals_conceded,
)

log = get_logger(__name__)


def _override_pts(overrides_df: pd.DataFrame, code: str, season_id: str, gw: int) -> int | None:
    if overrides_df.empty:
        return None
    ov = overrides_df[
        (overrides_df["player_code"] == code)
        & (overrides_df["season_id"] == season_id)
        & (overrides_df["game_week"].astype(int) == gw)
    ]
    return int(ov.iloc[0]["override_points"]) if not ov.empty else None


def _compute_best_gameweeks(store: CsvStore) -> pd.DataFrame:
    selections_df = store.read("manager_selections")
    goals_df = store.read("goals")
    overrides_df = store.read("overrides")
    players_df = store.read_all_players()
    seasons_df = store.read("seasons")

    last_gw_map = {
        r["season_id"]: int(r["last_gw_synced"]) if str(r.get("last_gw_synced", "")) not in ("", "nan") else 38
        for _, r in seasons_df.iterrows()
    }

    rows = []
    for manager_name in selections_df["manager_name"].unique():
        mgr_sels = selections_df[selections_df["manager_name"] == manager_name]
        best_pts, best_label = 0, "—"
        for season_id in mgr_sels["season_id"].unique():
            last_gw = last_gw_map.get(season_id, 38)
            season_sels = mgr_sels[mgr_sels["season_id"] == season_id]
            for gw in range(1, last_gw + 1):
                gw_pts = 0
                for _, s in season_sels.iterrows():
                    gw_from = _int(s["gw_from"])
                    gw_to_raw = s.get("gw_to", "")
                    gw_to = last_gw if str(gw_to_raw) in ("", "nan") or pd.isna(gw_to_raw) else min(_int(gw_to_raw), last_gw)
                    if not (gw_from <= gw <= gw_to):
                        continue
                    code = s["player_code"]
                    pos = s["position"].upper()
                    ov = _override_pts(overrides_df, code, season_id, gw)
                    if ov is not None:
                        gw_pts += ov
                    elif pos == "GK":
                        conceded = _goals_for(goals_df, code, season_id, gw, "goals_conceded")
                        team_id = _int(code.split("-")[-1])
                        gk_codes = _gk_player_codes_for_team(players_df, team_id, season_id)
                        gk_goals = sum(_goals_for(goals_df, c, season_id, gw, "goals_scored") for c in gk_codes)
                        gw_pts += score_gk_team(conceded, gk_goals)
                    else:
                        goals = _goals_for(goals_df, code, season_id, gw, "goals_scored")
                        gw_pts += int(score_outfield_player(pos, goals))
                if gw_pts > best_pts:
                    best_pts = gw_pts
                    best_label = f"GW{gw} {season_id}"
        rows.append({"manager_name": manager_name, "label": best_label, "pts": str(int(best_pts))})
    return pd.DataFrame(rows)


@click.command()
@click.option("--dry-run", is_flag=True, help="Fetch from FPL but do not write to CSVs")
@click.option("--force", is_flag=True, help="Skip gameweek-in-progress guard and sync anyway")
def main(dry_run: bool, force: bool) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    store = CsvStore(settings.data_dir)
    season_id = store.current_season()

    log.info("sync_scores_start", season=season_id, dry_run=dry_run, force=force)

    with FplClient(settings.fpl_base_url) as client:
        bootstrap = client.get_bootstrap()

        # Always sync players/teams — useful pre-season for auction preparation
        sync_players(bootstrap, season_id, store, dry_run=dry_run)

        # Split elements into GK players and outfield players
        gk_elements = [e for e in bootstrap.elements if e.element_type == 1]
        outfield_elements = [e for e in bootstrap.elements if e.element_type != 1]

        # Skip goal sync if a gameweek is currently in progress (data incomplete)
        if not force and is_gw_in_progress(bootstrap):
            log.warning("goals_sync_skipped", reason="gameweek_in_progress")
            raise SystemExit(0)

        # Skip goal sync if no gameweek has finished yet (pre-season)
        if not is_season_in_progress(bootstrap):
            log.info("goals_sync_skipped", reason="no_gameweek_finished_yet")
            raise SystemExit(0)

        # Sync outfield player goals
        for element in outfield_elements:
            try:
                sync_outfield_goals(client, element, season_id, store, dry_run=dry_run)
            except Exception:
                log.exception("outfield_sync_failed", player_id=element.id)

        # Sync GK team goals conceded — one request per team
        team_ids = {e.team for e in bootstrap.elements}
        for team_id in sorted(team_ids):
            try:
                sync_team_goals_conceded(client, team_id, season_id, store, dry_run=dry_run)
            except Exception:
                log.exception("gk_team_sync_failed", team_id=team_id)

        # Sync GK player goals (for the +4 bonus)
        try:
            sync_gk_player_goals(client, gk_elements, season_id, store, dry_run=dry_run)
        except Exception:
            log.exception("gk_player_goals_sync_failed")

        gw = last_synced_gw(bootstrap)
        if gw and not dry_run:
            seasons_df = store.read("seasons")
            seasons_df.loc[seasons_df["season_id"] == season_id, "last_gw_synced"] = str(gw)
            store.write("seasons", seasons_df)
            log.info("last_gw_synced_updated", gw=gw)

            # Pre-compute standings so the dashboard reads a file instead of computing
            prizes_df = store.read("prizes")
            season_prizes = [
                Prize(season_id=r["season_id"], position=int(r["position"]), prize_amount=float(r["prize_amount"]))
                for _, r in prizes_df[prizes_df["season_id"] == season_id].iterrows()
            ]
            standings = compute_standings(
                season_id=season_id,
                up_to_gw=gw,
                selections_df=store.read("manager_selections"),
                goals_df=store.read("goals"),
                overrides_df=store.read("overrides"),
                players_df=store.read_all_players(),
                prizes=season_prizes,
            )
            standings_rows = [
                {
                    "season_id": season_id,
                    "position": s.position,
                    "manager_name": s.manager_name,
                    "total_points": s.total_points,
                    "prize": s.prize if s.prize is not None else "",
                }
                for s in standings
            ]
            store.upsert("standings", pd.DataFrame(standings_rows), key_cols=["season_id", "manager_name"])

            log.info("standings_precomputed", managers=len(standings), up_to_gw=gw)

            best_gw_df = _compute_best_gameweeks(store)
            store.write("best_gameweeks", best_gw_df)
            log.info("best_gameweeks_precomputed", managers=len(best_gw_df))

    log.info("sync_scores_complete", season=season_id, dry_run=dry_run)
