"""
Migrate data from vaastav element IDs to current FPL element IDs.

Steps:
1. Build mapping from 2024-25 historical codes -> current codes (by friendly_name)
2. Update manager_selections.csv 2024-25 rows to use current element IDs
3. Delete ALL player + goal entries for seasons 2018-19 through 2024-25
4. After running, re-run: uv run python scripts/import_fpl_history.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from select_football.common.csv_store import CsvStore
from select_football.config import get_settings

HISTORICAL_SEASONS = ["2018-19", "2019-20", "2020-21", "2021-22", "2022-23", "2023-24", "2024-25"]


def main() -> None:
    settings = get_settings()
    store = CsvStore(settings.data_dir)

    players_df = store.read("players")
    goals_df = store.read("goals")
    sel_df = store.read("manager_selections")

    # ── Build code migration map for 2024-25 ──────────────────────────────────
    # current (2025-26) friendly_name -> element_id
    current_players = players_df[
        (players_df["season"] == "2025-26") & (players_df["type"] == "player")
    ]
    curr_name_to_id: dict[str, str] = {
        r["friendly_name"]: str(r["element_id"])
        for _, r in current_players.iterrows()
    }

    # For 2024-25 player entries, build old_code -> new_code where IDs differ
    p2425 = players_df[(players_df["season"] == "2024-25") & (players_df["type"] == "player")]
    code_migration: dict[str, str] = {}
    unmapped: list[str] = []
    seen: set[str] = set()
    for _, row in p2425.iterrows():
        fname = row["friendly_name"]
        old_id = str(row["element_id"])
        if fname in seen:
            continue
        seen.add(fname)
        if fname in curr_name_to_id:
            curr_id = curr_name_to_id[fname]
            if old_id != curr_id:
                old_code = f"2024-25-player-{old_id}"
                new_code = f"2024-25-player-{curr_id}"
                code_migration[old_code] = new_code
        else:
            unmapped.append(f"2024-25-player-{old_id} ({fname})")

    print(f"Code migrations built: {len(code_migration)}")
    if unmapped:
        print(f"Ex-PL players with no current mapping ({len(unmapped)}) — codes kept as-is:")
        for u in unmapped:
            print(f"  {u}")

    # ── Update manager_selections.csv ─────────────────────────────────────────
    if not sel_df.empty:
        mask_2425 = sel_df["season_id"] == "2024-25"
        before = sel_df.loc[mask_2425, "player_code"].tolist()
        sel_df.loc[mask_2425, "player_code"] = sel_df.loc[mask_2425, "player_code"].apply(
            lambda c: code_migration.get(c, c)
        )
        after = sel_df.loc[mask_2425, "player_code"].tolist()
        changed = sum(1 for a, b in zip(before, after) if a != b)
        print(f"\nUpdated {changed} manager_selections rows for 2024-25")
        store.write("manager_selections", sel_df)

    # ── Delete historical player entries ──────────────────────────────────────
    hist_player_mask = (
        players_df["season"].isin(HISTORICAL_SEASONS) &
        (players_df["type"] == "player")
    )
    n_players = int(hist_player_mask.sum())
    players_df = players_df[~hist_player_mask]
    print(f"\nRemoved {n_players} historical player entries (all seasons 2018-19 through 2024-25)")

    # Keep team entries — they're needed for GK selection display
    store.write("players", players_df)

    # ── Delete all historical goal entries ────────────────────────────────────
    hist_goal_mask = goals_df["season_id"].isin(HISTORICAL_SEASONS)
    n_goals = int(hist_goal_mask.sum())
    goals_df = goals_df[~hist_goal_mask]
    print(f"Removed {n_goals} historical goal entries (per-GW and season totals)")
    store.write("goals", goals_df)

    print("\nDone. Now run:")
    print("  uv run python scripts/import_fpl_history.py")
    print("to rebuild historical player + season-total goal data from the FPL API.")


if __name__ == "__main__":
    main()
