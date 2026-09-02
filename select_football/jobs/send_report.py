"""Job: compute current standings and email the league table.

Usage:
    uv run send-report
    uv run send-report --preview          # print HTML to stdout, do not send
    uv run send-report --force            # send even if already sent this month
    uv run send-report --to me@example.com  # test send to just me; guards off,
                                           # send is not recorded
"""
import json
import re
from datetime import date
from pathlib import Path

import click

from select_football.common.csv_store import CsvStore
from select_football.common.logging import configure_logging, get_logger
from select_football.common.models import ManagerStanding, Prize
from select_football.config import get_settings
from select_football.core.standings import compute_standings
from select_football.email.renderer import render_report, render_report_text
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


def _parse_manager_emails(raw: str) -> list[str]:
    """Pull addresses out of the MANAGER_EMAILS value.

    Handles the "name,email" CSV text stored in the GitHub secret as well as a
    bare list of addresses separated by commas, semicolons or newlines. Any
    token without an "@" (the header, the name column) is ignored.
    """
    tokens = re.split(r"[,;\n\r]+", raw or "")
    return [t.strip() for t in tokens if "@" in t]


def _get_recipients(settings) -> list[str]:
    if settings.send_test_only:
        return [settings.test_email]

    return _parse_manager_emails(settings.manager_emails)


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


def _current_month() -> str:
    return date.today().strftime("%Y-%m")


def _movement_baseline(config: dict) -> list[dict] | None:
    """The most recent monthly snapshot from a month before this one, or None."""
    history: dict = config.get("email_standings_history", {})
    past = sorted(m for m in history if m < _current_month())
    return history[past[-1]] if past else None


def _compute_movement(
    standings: list[ManagerStanding], previous: list[dict]
) -> dict[str, int | None]:
    """manager -> places gained since `previous` (+ve = up, None = not in it)."""
    prev_pos = {row["manager_name"]: int(row["position"]) for row in previous}
    return {
        s.manager_name: (prev_pos[s.manager_name] - s.position)
        if s.manager_name in prev_pos
        else None
        for s in standings
    }


def _record_send(store: CsvStore, standings: list[ManagerStanding]) -> None:
    """Mark the email sent this month and snapshot the table for next month's movement."""
    config = _read_config(store)
    config["last_email_sent"] = date.today().isoformat()

    history: dict = config.get("email_standings_history", {})
    history[_current_month()] = [
        {"position": s.position, "manager_name": s.manager_name} for s in standings
    ]
    for old in sorted(history)[:-3]:  # keep the 3 most recent months
        del history[old]
    config["email_standings_history"] = history

    _write_config(store, config)


@click.command()
@click.option("--preview", is_flag=True, help="Print rendered HTML to stdout instead of sending")
@click.option("--force", is_flag=True, help="Send even if already sent this month")
@click.option(
    "--to",
    "to_addrs",
    multiple=True,
    metavar="EMAIL",
    help="Send only to these addresses (repeatable). A test send: skips the "
    "monthly / gameweek guards and does NOT record the send, so the real "
    "monthly email still goes out later.",
)
def main(preview: bool, force: bool, to_addrs: tuple[str, ...]) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    store = CsvStore(settings.data_dir)
    season_id = store.current_season()

    is_test = bool(to_addrs)
    log.info(
        "send_report_start",
        season=season_id,
        preview=preview,
        force=force,
        test=is_test,
    )

    if not force and not preview and not is_test and _already_sent_this_month(store, season_id):
        log.warning("send_skipped", reason="already_sent_this_month")
        raise SystemExit(0)

    if not force and not preview and not is_test:
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

    baseline = _movement_baseline(_read_config(store))
    movement = _compute_movement(standings, baseline) if baseline else None

    html = render_report(
        standings,
        season_id=season_id,
        gameweek=current_gw,
        movement=movement,
    )

    if preview:
        click.echo(html)
        return

    recipients = list(to_addrs) if is_test else _get_recipients(settings)
    if not recipients:
        log.error("no_recipients_found")
        raise SystemExit(1)

    text = render_report_text(standings, season_id=season_id, gameweek=current_gw)

    subject = f"Select Fantasy Football League Table {season_id}"
    if is_test:
        subject = f"[TEST] {subject}"
    send_report(html, recipients, subject, settings, text_body=text)

    if is_test:
        log.info("send_report_test_complete", season=season_id, recipients=recipients)
        return

    _record_send(store, standings)
    log.info("send_report_complete", season=season_id, recipients=len(recipients))


if __name__ == "__main__":
    main()
