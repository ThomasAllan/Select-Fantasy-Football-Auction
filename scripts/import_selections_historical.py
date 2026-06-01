"""
Import historical manager selections (2020-21, 2021-22) from selections_historical_raw.tsv.

Matches player full_name and GK team name against players.csv for each season.
Skips GKG rows (GK player goal bonus — handled automatically by the scoring engine).
"""
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd

from select_football.common.csv_store import CsvStore
from select_football.config import get_settings

RAW_TSV = Path(__file__).parent / "selections_historical_raw.tsv"

# Common short-form aliases used in the TSV that may differ from vaastav team names
TEAM_ALIASES: dict[str, str] = {
    "man utd": "manchester united",
    "man city": "manchester city",
    "spurs": "tottenham hotspur",
    "wolves": "wolverhampton wanderers",
    "sheffield utd": "sheffield united",
    "west brom": "west bromwich albion",
    "leeds": "leeds united",
    "villa": "aston villa",
    "leicester": "leicester city",
    "newcastle": "newcastle united",
    "brighton": "brighton and hove albion",
    "norwich": "norwich city",
    "brentford": "brentford",
    "crystal palace": "crystal palace",
    "southampton": "southampton",
    "everton": "everton",
    "burnley": "burnley",
    "arsenal": "arsenal",
    "chelsea": "chelsea",
    "liverpool": "liverpool",
    "west ham": "west ham united",
    "fulham": "fulham",
    "watford": "watford",
}


def _normalise(s: str) -> str:
    """Lower-case, strip accents, collapse whitespace."""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return " ".join(s.lower().split())


def _resolve_team(raw_name: str, team_name_to_code: dict[str, str]) -> str | None:
    """Try normalised name, then alias expansion."""
    key = _normalise(raw_name)
    code = team_name_to_code.get(key)
    if code:
        return code
    expanded = TEAM_ALIASES.get(key)
    if expanded:
        return team_name_to_code.get(expanded)
    return None


def main() -> None:
    settings = get_settings()
    store = CsvStore(settings.data_dir)
    players_df = store.read("players")

    # Build per-season lookups
    season_team_lookup: dict[str, dict[str, str]] = {}   # season -> {norm_name: code}
    season_player_lookup: dict[str, dict[str, str]] = {}  # season -> {norm_full_name: code}

    for season_id in players_df["season"].unique():
        p_season = players_df[players_df["season"] == season_id]

        teams = p_season[p_season["type"] == "team"]
        team_map: dict[str, str] = {}
        for _, r in teams.iterrows():
            for name_field in ("friendly_name", "full_name"):
                val = str(r.get(name_field, "") or "")
                if val:
                    team_map[_normalise(val)] = r["code"]
        season_team_lookup[season_id] = team_map

        players = p_season[p_season["type"] == "player"].copy()
        # Sort ascending by element_id so the lower (canonical) ID wins when a player
        # has duplicate full_name entries (ghost entries always have higher IDs, 0 goals).
        players["_eid_int"] = pd.to_numeric(players["element_id"], errors="coerce")
        players = players.sort_values("_eid_int")
        player_map: dict[str, str] = {}
        for _, r in players.iterrows():
            fn = str(r.get("full_name", "") or "")
            if fn:
                nfn = _normalise(fn)
                if nfn not in player_map:
                    player_map[nfn] = r["code"]
        season_player_lookup[season_id] = player_map


    rows: list[dict] = []
    unmatched: list[str] = []

    with open(RAW_TSV, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 8:
                continue
            _orig_code, season_id, manager, name, position, cost, gw_from, gw_to = parts[:8]
            position = position.upper()

            # Skip GKG rows — scoring engine derives GK player goal bonus automatically
            if position == "GKG":
                continue

            # Strip £ prefix from cost
            cost = cost.lstrip("£").strip()

            team_map = season_team_lookup.get(season_id, {})
            player_map = season_player_lookup.get(season_id, {})

            if position == "GK":
                code = _resolve_team(name, team_map)
                if not code:
                    unmatched.append(f"[{season_id}] GK team '{name}' not found")
                    continue
            else:
                norm_name = _normalise(name)
                code = player_map.get(norm_name)
                if not code:
                    unmatched.append(f"[{season_id}] player '{name}' ({manager}) not found")
                    continue

            rows.append({
                "player_code": code,
                "season_id": season_id,
                "manager_name": manager,
                "position": position,
                "cost": cost,
                "gw_from": gw_from,
                "gw_to": gw_to,
            })

    print(f"Matched: {len(rows)}  |  Unmatched: {len(unmatched)}")
    if unmatched:
        print("\nUnmatched rows:")
        for u in unmatched:
            print(f"  {u}")

    if not rows:
        print("Nothing to write.")
        return

    sel_df = pd.DataFrame(rows)
    store.upsert(
        "manager_selections",
        sel_df,
        key_cols=["player_code", "season_id", "manager_name", "gw_from"],
    )
    print(f"\nWritten {len(sel_df)} rows to manager_selections.csv")

    print("\nSelections per manager per season:")
    print(sel_df.groupby(["season_id", "manager_name"]).size().to_string())


if __name__ == "__main__":
    main()
