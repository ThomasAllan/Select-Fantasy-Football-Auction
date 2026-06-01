"""
Import historical FPL season totals for all current players.

Fetches element-summary history_past from the FPL API and stores:
  - Player entries in players.csv (keyed by current 2025-26 element IDs)
  - Season-total goals in goals.csv (game_week="0")

This replaces the vaastav-based import for historical seasons, ensuring
player codes in manager_selections.csv (which use current FPL element IDs)
correctly match entries in players.csv and goals.csv.

GK team goals_conceded are approximated by summing the goals_conceded from
all GK players that currently play for that team.

Usage:
    uv run python scripts/import_fpl_history.py
    uv run python scripts/import_fpl_history.py --seasons 2024-25
    uv run python scripts/import_fpl_history.py --dry-run
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
import pandas as pd

from select_football.common.csv_store import CsvStore
from select_football.config import get_settings

FPL_BASE = "https://fantasy.premierleague.com/api"

SEASON_NAME_MAP = {
    "2018/19": "2018-19",
    "2019/20": "2019-20",
    "2020/21": "2020-21",
    "2021/22": "2021-22",
    "2022/23": "2022-23",
    "2023/24": "2023-24",
    "2024/25": "2024-25",
}

DEFAULT_SEASONS = list(SEASON_NAME_MAP.values())

ELEMENT_TYPE_MAP = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--seasons", nargs="+", default=DEFAULT_SEASONS, metavar="SEASON",
        help=f"Season IDs to import (default: all)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Fetch only, no writes")
    args = parser.parse_args()

    target_seasons = set(args.seasons)

    settings = get_settings()
    store = CsvStore(settings.data_dir)

    with httpx.Client(
        timeout=30,
        headers={"User-Agent": "select-football-auction/1.0"},
        follow_redirects=True,
    ) as client:
        print("Fetching FPL bootstrap...")
        bootstrap = client.get(f"{FPL_BASE}/bootstrap-static/").json()
        elements = bootstrap["elements"]
        teams_map = {t["id"]: t["name"] for t in bootstrap["teams"]}
        print(f"Found {len(elements)} players, {len(teams_map)} teams")

        player_rows: list[dict] = []
        goal_rows: list[dict] = []
        team_conceded: dict[tuple, int] = {}  # (team_id_str, season_id) → total conceded

        for i, elem in enumerate(elements):
            if i > 0 and i % 100 == 0:
                print(f"  {i}/{len(elements)} players processed...")

            elem_id = elem["id"]
            team_id = elem["team"]
            fpl_position = ELEMENT_TYPE_MAP.get(elem["element_type"], "")
            is_gk = elem["element_type"] == 1

            try:
                summary = client.get(f"{FPL_BASE}/element-summary/{elem_id}/").json()
                time.sleep(0.05)
            except Exception as e:
                print(f"  Warning: could not fetch element {elem_id}: {e}")
                continue

            for past in summary.get("history_past", []):
                season_id = SEASON_NAME_MAP.get(past.get("season_name", ""))
                if not season_id or season_id not in target_seasons:
                    continue

                code = f"{season_id}-player-{elem_id}"
                goals_scored = int(past.get("goals_scored", 0) or 0)
                goals_conceded_player = int(past.get("goals_conceded", 0) or 0)

                player_rows.append({
                    "code": code,
                    "season": season_id,
                    "type": "player",
                    "element_id": str(elem_id),
                    "full_name": f"{elem['first_name']} {elem['second_name']}",
                    "friendly_name": elem["web_name"],
                    "fpl_position": fpl_position,
                    "team_id": str(team_id),
                    "team_code": str(elem.get("team_code", "")),
                    "status": "A",
                    "news": "",
                    "news_date": "",
                    "photo_url": (
                        f"https://resources.premierleague.com/premierleague25/photos/players/110x140/"
                        f"{elem['photo'].replace('.jpg', '.png')}"
                    ),
                })

                # game_week="0" is a season-total sentinel used by the dashboard.
                # Always write a row (even 0 goals) so the dashboard knows the player
                # existed in that season.
                goal_rows.append({
                    "player_code": code,
                    "season_id": season_id,
                    "game_week": "0",
                    "goals_scored": str(goals_scored),
                    "goals_conceded": "0",
                })

                # Accumulate GK team goals conceded (sum across all GK players per team).
                # Uses current team as proxy — slightly inaccurate for players who moved.
                if is_gk and goals_conceded_player > 0:
                    key = (str(team_id), season_id)
                    team_conceded[key] = team_conceded.get(key, 0) + goals_conceded_player

        # Add GK team season-total goals conceded entries
        for (team_id_str, season_id), total_conceded in team_conceded.items():
            goal_rows.append({
                "player_code": f"{season_id}-team-{team_id_str}",
                "season_id": season_id,
                "game_week": "0",
                "goals_scored": "0",
                "goals_conceded": str(total_conceded),
            })

        print(
            f"\nCollected: {len(player_rows)} player entries, {len(goal_rows)} goal entries "
            f"({len(team_conceded)} GK team conceded)"
        )

        if args.dry_run:
            print("Dry run — no writes.")
            return

        # ── Remove stale vaastav-based entries ────────────────────────────────
        # Only remove entries for players who ARE in the new import (same friendly_name)
        # but had different old codes. Preserve entries for ex-PL players (not in new import).
        players_df = store.read("players")
        if not players_df.empty:
            new_codes = {r["code"] for r in player_rows}
            new_friendly_names = {r["friendly_name"] for r in player_rows}
            for season_id in target_seasons:
                mask = (
                    (players_df["season"] == season_id) &
                    (players_df["type"] == "player") &
                    (~players_df["code"].isin(new_codes)) &
                    (players_df["friendly_name"].isin(new_friendly_names))
                )
                n = int(mask.sum())
                if n:
                    players_df = players_df[~mask]
                    print(f"  Removed {n} stale player entries for {season_id}")
            store.write("players", players_df)

        goals_df = store.read("goals")
        if not goals_df.empty:
            for season_id in target_seasons:
                # Remove per-GW entries (game_week != "0") for this season
                mask = (
                    (goals_df["season_id"] == season_id) &
                    (goals_df["player_code"].str.startswith(season_id + "-")) &
                    (goals_df["game_week"] != "0")
                )
                n = int(mask.sum())
                if n:
                    goals_df = goals_df[~mask]
                    print(f"  Removed {n} old per-GW goal entries for {season_id}")
            store.write("goals", goals_df)

        # ── Write new data ─────────────────────────────────────────────────────
        store.upsert("players", pd.DataFrame(player_rows), key_cols=["code"])
        store.upsert("goals", pd.DataFrame(goal_rows), key_cols=["player_code", "season_id", "game_week"])

        print("\nDone! Run `uv run send-report --preview` to verify standings.")


if __name__ == "__main__":
    main()
