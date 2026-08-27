"""Job: compute current standings and email the league table.

Usage:
    uv run send-report
    uv run send-report --preview          # print HTML to stdout, do not send
    uv run send-report --force            # send even if already sent this month
"""
import json
from datetime import date
from pathlib import Path

import click

from select_football.common.csv_store import CsvStore
from select_football.common.logging import configure_logging, get_logger
from select_football.common.models import Prize
from select_football.config import get_settings
from select_football.core.standings import compute_standings
from select_football.email.renderer import render_report
from select_football.email.sender import send_report
from select_football.fpl.client import FplClient
from select_football.fpl.sync import is_gw_in_progress

log = get_logger(__name__)


def _load_prizes(store: CsvStore, season_id: str) -> list[Prize]:
    df = store.read("prizes")
    if df.empty:
        return []
    rows = df[df["season_id"] == season_id]
    return [
        Prize(
            season_id=r["season_id"],
            position=int(r["position"]),
            prize_amount=float(r["prize_amount"]),
        )
        for _, r in rows.iterrows()
    ]


def _get_recipients(store: CsvStore, season_id: str, settings) -> list[str]:
    if settings.send_test_only:
        return [settings.test_email]

    df = store.read("manager_emails")
    if df.empty or "email" not in df.columns:
        return []
    return [e for e in df["email"].dropna().tolist() if e]


def _config_path(store: CsvStore) -> Path:
    return Path(store.data_dir) / "config.json"


def _read_config(store: CsvStore) -> dict:
    path = _config_path(store)
    if path.exists():
        return json.loads(path.read_text())
    return {}


def _write_config(store: CsvStore, config: dict) -> None:
    _config_path(store).write_text(json.dumps(config, indent=2))


def _already_sent_this_month(store: CsvStore, season_id: str) -> bool:
    last_sent = _read_config(store).get("last_email_sent", "")
    if not last_sent:
        return False
    try:
        sent_date = date.fromisoformat(str(last_sent))
        today = date.today()
        return sent_date.year == today.year and sent_date.month == today.month
    except ValueError:
        return False


def _update_last_sent(store: CsvStore, season_id: str) -> None:
    config = _read_config(store)
    config["last_email_sent"] = date.today().isoformat()
    _write_config(store, config)


@click.command()
@click.option("--preview", is_flag=True, help="Print rendered HTML to stdout instead of sending")
@click.option("--force", is_flag=True, help="Send even if already sent this month")
def main(preview: bool, force: bool) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    store = CsvStore(settings.data_dir)
    season_id = store.current_season()

    log.info("send_report_start", season=season_id, preview=preview, force=force)

    if not force and not preview and _already_sent_this_month(store, season_id):
        log.warning("send_skipped", reason="already_sent_this_month")
        raise SystemExit(0)

    if not force and not preview:
        with FplClient(settings.fpl_base_url) as client:
            if is_gw_in_progress(client.get_bootstrap()):
                log.warning("send_skipped", reason="gameweek_in_progress")
                raise SystemExit(0)

    # Load all required DataFrames
    selections_df = store.read("manager_selections")
    goals_df = store.read("goals")
    overrides_df = store.read("overrides")
    players_df = store.read_all_players()
    seasons_df = store.read("seasons")

    # Determine current GW from seasons.csv
    season_rows = seasons_df[seasons_df["season_id"] == season_id]
    if season_rows.empty:
        log.error("season_not_found", season=season_id)
        raise SystemExit(1)
    current_gw_val = season_rows.iloc[0].get("last_gw_synced", "")
    if not current_gw_val:
        log.error("no_gw_synced", season=season_id, hint="run sync-scores first")
        raise SystemExit(1)
    current_gw = int(current_gw_val)

    prizes = _load_prizes(store, season_id)

    standings = compute_standings(
        season_id=season_id,
        up_to_gw=current_gw,
        selections_df=selections_df,
        goals_df=goals_df,
        overrides_df=overrides_df,
        players_df=players_df,
        prizes=prizes,
    )

    # Never email a headers-only table (e.g. a season with no selections imported yet).
    # --preview still renders so the layout can be inspected.
    if not standings and not preview:
        log.warning("send_skipped", reason="no_standings", season=season_id)
        raise SystemExit(0)

    html = render_report(standings)

    if preview:
        click.echo(html)
        return

    recipients = _get_recipients(store, season_id, settings)
    if not recipients:
        log.error("no_recipients_found")
        raise SystemExit(1)

    subject = f"Select Fantasy Football League Table {season_id}"
    send_report(html, recipients, subject, settings)

    _update_last_sent(store, season_id)
    log.info("send_report_complete", season=season_id, recipients=len(recipients))
