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
from select_football.core.standings import compute_standings
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

        if not force and is_gw_in_progress(bootstrap):
            log.warning("sync_skipped", reason="gameweek_in_progress")
            raise SystemExit(0)

        if not is_season_in_progress(bootstrap):
            log.warning("sync_skipped", reason="season_not_in_progress")
            raise SystemExit(0)

        # Sync player/team registry (includes status, news, news_date)
        sync_players(bootstrap, season_id, store, dry_run=dry_run)

        # Split elements into GK players and outfield players
        gk_elements = [e for e in bootstrap.elements if e.element_type == 1]
        outfield_elements = [e for e in bootstrap.elements if e.element_type != 1]

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

            gw_rows = [
                {"season_id": season_id, "manager_name": s.manager_name, "game_week": str(gw_num), "points": pts}
                for s in standings
                for gw_num, pts in s.gw_breakdown.items()
            ]
            store.upsert("manager_gw_points", pd.DataFrame(gw_rows), key_cols=["season_id", "manager_name", "game_week"])
            log.info("standings_precomputed", managers=len(standings), up_to_gw=gw)

    log.info("sync_scores_complete", season=season_id, dry_run=dry_run)
