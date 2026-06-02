"""
Compute and persist standings for all historical seasons that have selections + goals data.

Run this after importing historical selections or goals to populate standings.csv
so the dashboard league table shows those seasons.

Usage:
    uv run python scripts/compute_historical_standings.py
    uv run python scripts/compute_historical_standings.py --seasons 2020-21 2021-22
    uv run python scripts/compute_historical_standings.py --dry-run
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd

from select_football.common.csv_store import CsvStore
from select_football.common.models import Prize
from select_football.config import get_settings
from select_football.core.standings import compute_standings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seasons", nargs="+", metavar="SEASON", help="Season IDs to process (default: all)")
    parser.add_argument("--dry-run", action="store_true", help="Compute but do not write to standings.csv")
    args = parser.parse_args()

    settings = get_settings()
    store = CsvStore(settings.data_dir)

    seasons_df = store.read("seasons")
    managers_df = store.read("managers")
    selections_df = store.read("manager_selections")
    goals_df = store.read("goals")
    overrides_df = store.read("overrides")
    players_df = store.read("players")
    prizes_df = store.read("prizes")

    candidate_seasons = args.seasons if args.seasons else seasons_df["season_id"].tolist()

    all_rows: list[dict] = []
    for season_id in candidate_seasons:
        season_row = seasons_df[seasons_df["season_id"] == season_id]
        if season_row.empty:
            print(f"{season_id}: not in seasons.csv — skipping")
            continue

        last_gw_raw = season_row.iloc[0].get("last_gw_synced", "")
        if not last_gw_raw or str(last_gw_raw) in ("", "nan"):
            print(f"{season_id}: no last_gw_synced — skipping")
            continue
        up_to_gw = int(last_gw_raw)

        season_sels = selections_df[selections_df["season_id"] == season_id]
        if season_sels.empty:
            print(f"{season_id}: no selections — skipping")
            continue

        prizes = [
            Prize(season_id=r["season_id"], position=int(r["position"]), prize_amount=float(r["prize_amount"]))
            for _, r in prizes_df[prizes_df["season_id"] == season_id].iterrows()
        ]

        standings = compute_standings(
            season_id=season_id,
            up_to_gw=up_to_gw,
            selections_df=selections_df,
            goals_df=goals_df,
            overrides_df=overrides_df,
            players_df=players_df,
            prizes=prizes,
        )

        print(f"{season_id}: {len(standings)} managers, top: {standings[0].manager_name} ({standings[0].total_points:.0f} pts)")

        for s in standings:
            all_rows.append({
                "season_id": season_id,
                "position": str(s.position),
                "manager_name": s.manager_name,
                "total_points": str(s.total_points),
                "prize": str(s.prize) if s.prize else "",
            })

    if not all_rows:
        print("Nothing to write.")
        return

    if args.dry_run:
        print(f"\nDry run — would write {len(all_rows)} rows.")
        return

    store.upsert(
        "standings",
        pd.DataFrame(all_rows),
        key_cols=["season_id", "manager_name"],
    )
    print(f"\nWritten {len(all_rows)} rows to standings.csv")


if __name__ == "__main__":
    main()
