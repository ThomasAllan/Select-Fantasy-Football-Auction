"""
Import manager selections and per-GW goal data for seasons 2009-10 through 2017-18
from FOOTY2009.xls through FOOTY2017.xls.

Formation is always GK + 3 DEF + 4 MID + 3 FWD (rows 7-17 in each manager sheet).
Per-GW values are already points: back-calculated to goals for outfield players.
GK values are stored as override_points (they bundle goals_conceded + any GK bonus).

Player codes:
  GK:      {season_id}-team-{CanonicalTeamName}
  Outfield: {season_id}-player-{raw player string from spreadsheet}

No entries are added to players.csv.

Usage:
    uv run python scripts/import_selections_2009_to_2017.py [--dry-run] [--year YEAR]
"""

import argparse
import sys
from pathlib import Path

import xlrd
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from manager_names import canonicalize

DATA_DIR = Path("data")

SEASON_MAP = {
    2009: "2009-10",
    2010: "2010-11",
    2011: "2011-12",
    2012: "2012-13",
    2013: "2013-14",
    2014: "2014-15",
    2015: "2015-16",
    2016: "2016-17",
    2017: "2017-18",
}

# (cost_col_idx, gw_col_start_idx) — layout changed in 2016
COL_FORMAT: dict[int, tuple[int, int]] = {
    2009: (2, 3), 2010: (2, 3), 2011: (2, 3), 2012: (2, 3),
    2013: (2, 3), 2014: (2, 3), 2015: (2, 3),
    2016: (1, 2), 2017: (1, 2),
}

POSITION_ORDER = ["GK", "DEF", "DEF", "DEF", "MID", "MID", "MID", "MID", "FWD", "FWD", "FWD"]
GOAL_MULTIPLIER = {"DEF": 3, "MID": 2, "FWD": 1}

SKIP_SHEETS = frozenset({
    "LeaderBoard", "Leftovers", "New Players", "Sheet1", "Sheet2", "ANOther", "Z's",
})

# Additional canonicalisations beyond what manager_names.py already covers
EXTRA_CANONICAL: dict[str, str] = {
    "James Wright": "Jamie Wright",
    "Wendy Thulbourn": "Wendy Thulbourne",
}

TEAM_ALIASES: dict[str, str] = {
    "arsenal": "Arsenal",
    "aston villa": "Aston Villa",
    "birmingham city": "Birmingham City",
    "birmingham": "Birmingham City",
    "blackburn": "Blackburn",
    "blackburn rovers": "Blackburn",
    "blackpool": "Blackpool",
    "bolton": "Bolton",
    "bolton wanderers": "Bolton",
    "bournemouth": "Bournemouth",
    "brighton": "Brighton",
    "brighton & hove albion": "Brighton",
    "brighton hove albion": "Brighton",
    "burnley": "Burnley",
    "cardiff city": "Cardiff City",
    "cardiff": "Cardiff City",
    "chelsea": "Chelsea",
    "crystal palace": "Crystal Palace",
    "c.palace": "Crystal Palace",
    "derby county": "Derby County",
    "derby": "Derby County",
    "everton": "Everton",
    "fulham": "Fulham",
    "hull city": "Hull City",
    "hull": "Hull City",
    "huddersfield town": "Huddersfield",
    "huddersfield": "Huddersfield",
    "ipswich town": "Ipswich",
    "ipswich": "Ipswich",
    "leeds united": "Leeds Utd",
    "leeds": "Leeds Utd",
    "leicester city": "Leicester",
    "leicester": "Leicester",
    "liverpool": "Liverpool",
    "man city": "Man City",
    "manchester city": "Man City",
    "man utd": "Man Utd",
    "man united": "Man Utd",
    "manchester united": "Man Utd",
    "middlesbrough": "Middlesbrough",
    "newcastle": "Newcastle",
    "newcastle united": "Newcastle",
    "newcastle utd": "Newcastle",
    "norwich city": "Norwich",
    "norwich": "Norwich",
    "portsmouth": "Portsmouth",
    "qpr": "QPR",
    "queens park rangers": "QPR",
    "reading": "Reading",
    "sheffield united": "Sheffield Utd",
    "sheffield utd": "Sheffield Utd",
    "southampton": "Southampton",
    "stoke city": "Stoke City",
    "stoke": "Stoke City",
    "sunderland": "Sunderland",
    "swansea city": "Swansea City",
    "swansea": "Swansea City",
    "tottenham hotspur": "Spurs",
    "tottenham": "Spurs",
    "spurs": "Spurs",
    "watford": "Watford",
    "west brom": "West Brom",
    "west bromwich albion": "West Brom",
    "west brom albion": "West Brom",
    "west ham": "West Ham",
    "west ham united": "West Ham",
    "wigan athletic": "Wigan",
    "wigan": "Wigan",
    "wolverhampton wanderers": "Wolves",
    "wolves": "Wolves",
    "wolverhampton": "Wolves",
    # Typos found in specific sheets
    "queen park rangers": "QPR",       # Steve Gale 2014: missing 's'
    "tottenham hotpsur": "Spurs",      # Ken Maggs 2014: transposed letters
    "watford town": "Watford",         # Karl Allen 2017
    "afc bournemouth": "Bournemouth",  # Tom Fowkes 2017
}


def normalize_team(raw: str) -> str:
    return TEAM_ALIASES.get(raw.strip().lower(), raw.strip())


def get_gw_cols(header_row: list, gw_col_start: int) -> list[tuple[int, int]]:
    """Return (col_index, gw_number) pairs for GW header columns."""
    result = []
    for i, v in enumerate(header_row):
        if i < gw_col_start:
            continue
        if isinstance(v, (int, float)) and v >= 1.0 and v <= 50 and v == int(v):
            result.append((i, int(v)))
    return result


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--year", type=int, choices=list(range(2009, 2018)),
                        help="Import only this year (e.g. 2009)")
    args = parser.parse_args()

    years = [args.year] if args.year else list(range(2009, 2018))

    all_selections: list[dict] = []
    all_goals: list[dict] = []
    all_overrides: list[dict] = []
    unmatched_teams: list[str] = []

    for year in years:
        season_id = SEASON_MAP[year]
        cost_col, gw_col_start = COL_FORMAT[year]
        xls_path = Path(f"historic_selections/FOOTY{year}.xls")

        print(f"\n{'='*60}")
        print(f"FOOTY{year}  ({season_id})")

        wb = xlrd.open_workbook(str(xls_path))

        for sheet_name in wb.sheet_names():
            if sheet_name in SKIP_SHEETS:
                continue

            ws = wb.sheet_by_name(sheet_name)

            raw_name = EXTRA_CANONICAL.get(sheet_name, sheet_name)
            manager = canonicalize(raw_name)

            header = ws.row_values(5)
            gw_cols = get_gw_cols(header, gw_col_start)
            if not gw_cols:
                print(f"  WARN: no GW columns found in sheet '{sheet_name}'")
                continue

            max_gw = max(gw for _, gw in gw_cols)
            print(f"\n  {manager}  ({len(gw_cols)} GWs, 1-{max_gw})")

            for pos_idx, position in enumerate(POSITION_ORDER):
                row_idx = 7 + pos_idx
                if row_idx >= ws.nrows:
                    print(f"    {position}: (row {row_idx} missing)")
                    continue

                row = ws.row_values(row_idx)
                player_str = str(row[0]).strip() if row[0] else ""
                if not player_str:
                    print(f"    {position}: (empty)")
                    continue

                cost_raw = row[cost_col] if cost_col < len(row) else ""
                cost = str(int(cost_raw)) if isinstance(cost_raw, float) else str(cost_raw or "")

                if position == "GK":
                    canonical_team = normalize_team(player_str)
                    if canonical_team == player_str.strip() and player_str.strip().lower() not in TEAM_ALIASES:
                        unmatched_teams.append(f"{year} | {manager} | {player_str!r}")
                    player_code = f"{season_id}-team-{canonical_team}"

                    for col_idx, gw_num in gw_cols:
                        val = row[col_idx] if col_idx < len(row) else 0.0
                        if isinstance(val, float) and val != 0.0:
                            all_overrides.append({
                                "player_code": player_code,
                                "season_id": season_id,
                                "game_week": str(gw_num),
                                "override_points": str(int(round(val))),
                            })
                    print(f"    GK   {player_str:35s} -> {player_code}  £{cost}")

                else:
                    player_code = f"{season_id}-player-{player_str}"
                    mult = GOAL_MULTIPLIER[position]

                    for col_idx, gw_num in gw_cols:
                        val = row[col_idx] if col_idx < len(row) else 0.0
                        if isinstance(val, float) and val > 0.0:
                            goals = int(round(val / mult))
                            if goals > 0:
                                all_goals.append({
                                    "player_code": player_code,
                                    "season_id": season_id,
                                    "game_week": str(gw_num),
                                    "goals_scored": str(goals),
                                    "goals_conceded": "0",
                                })
                    print(f"    {position:3s}  {player_str:35s} -> {player_code}  £{cost}")

                all_selections.append({
                    "player_code": player_code,
                    "season_id": season_id,
                    "manager_name": manager,
                    "position": position,
                    "cost": cost,
                    "gw_from": "1",
                    "gw_to": str(max_gw),
                })

    print(f"\n{'='*60}")
    print(f"Selections : {len(all_selections)}")
    print(f"Goal records: {len(all_goals)}")
    print(f"Overrides  : {len(all_overrides)}")

    if unmatched_teams:
        print("\nUnrecognised GK team names (add to TEAM_ALIASES):")
        for t in unmatched_teams:
            print(f"  {t}")

    if args.dry_run:
        print("\nDry run — no files written.")
        return

    if not (all_selections or all_goals or all_overrides):
        print("Nothing to write.")
        return

    seasons_imported = {SEASON_MAP[y] for y in years}

    # manager_selections.csv
    sel_path = DATA_DIR / "manager_selections.csv"
    if sel_path.exists():
        existing = pd.read_csv(sel_path, dtype=str).fillna("")
        existing = existing[~existing["season_id"].isin(seasons_imported)]
        result = pd.concat([existing, pd.DataFrame(all_selections)], ignore_index=True)
    else:
        result = pd.DataFrame(all_selections)
    result.to_csv(sel_path, index=False)
    print(f"\nWritten {len(all_selections)} rows -> manager_selections.csv")

    # goals.csv
    if all_goals:
        goals_path = DATA_DIR / "goals.csv"
        if goals_path.exists():
            existing_g = pd.read_csv(goals_path, dtype=str).fillna("")
            existing_g = existing_g[~existing_g["season_id"].isin(seasons_imported)]
            result_g = pd.concat([existing_g, pd.DataFrame(all_goals)], ignore_index=True)
        else:
            result_g = pd.DataFrame(all_goals)
        result_g.to_csv(goals_path, index=False)
        print(f"Written {len(all_goals)} rows -> goals.csv")

    # overrides.csv
    if all_overrides:
        ov_path = DATA_DIR / "overrides.csv"
        if ov_path.exists():
            existing_ov = pd.read_csv(ov_path, dtype=str).fillna("")
            existing_ov = existing_ov[~existing_ov["season_id"].isin(seasons_imported)]
            result_ov = pd.concat([existing_ov, pd.DataFrame(all_overrides)], ignore_index=True)
        else:
            result_ov = pd.DataFrame(all_overrides)
        result_ov.to_csv(ov_path, index=False)
        print(f"Written {len(all_overrides)} rows -> overrides.csv")


if __name__ == "__main__":
    main()
