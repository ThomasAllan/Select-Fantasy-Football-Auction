"""
Import 2020-21 manager selections from FOOTY2020.xls into manager_selections.csv.

This season uses full player names (not "I.Surname" format).
Neil Wright did not participate this season.
Karl Allen and Jamie Blunt participated (historical managers).

Usage:
    uv run python scripts/import_selections_2020_21.py [--dry-run]
"""

import argparse
import unicodedata
from pathlib import Path

import sys
import xlrd
import pandas as pd

DATA_DIR = Path("data")
XLS_PATH = Path("historic_selections/FOOTY2020.xls")
SEASON_ID = "2020-21"

MANAGER_SHEETS = [
    "Thomas Allan", "Karl Allen", "Jamie Blunt", "Andrea Chapman", "Andy Fowkes",
    "Tom Fowkes", "Steve Gale", "Pam Hart", "Tim Hart", "Mick Jones", "Ken Maggs",
    "Gary Speechley", "Kev Thulbourne", "Wendy Thulbourne", "Jamie Wright",
    # Neil Wright did not play this season
]

SHEET_NAME_OVERRIDES = {"Thomas Allan": "Tom Allan"}

TEAM_ALIASES: dict[str, str] = {
    "arsenal": "Arsenal", "aston villa": "Aston Villa", "villa": "Aston Villa",
    "bha": "Brighton", "brighton": "Brighton", "brighton ha": "Brighton",
    "burnley": "Burnley", "chelsea": "Chelsea",
    "c.palace": "Crystal Palace", "crystal palace": "Crystal Palace", "palace": "Crystal Palace",
    "everton": "Everton", "fulham": "Fulham",
    "leeds": "Leeds", "leeds utd": "Leeds", "leeds united": "Leeds",
    "leicester": "Leicester", "leicester city": "Leicester",
    "liverpool": "Liverpool",
    "man city": "Man City", "manchester city": "Man City",
    "man utd": "Man Utd", "man united": "Man Utd", "manchester united": "Man Utd",
    "newcastle": "Newcastle", "newcastle utd": "Newcastle", "newcastle united": "Newcastle",
    "sheffield utd": "Sheffield Utd", "sheffield united": "Sheffield Utd", "sheff utd": "Sheffield Utd",
    "southampton": "Southampton",
    "spurs": "Spurs", "tottenham": "Spurs", "tottenham hotspur": "Spurs",
    "west brom": "West Brom", "west bromwich albion": "West Brom",
    "west ham": "West Ham", "west ham utd": "West Ham", "west ham united": "West Ham",
    "wolves": "Wolves", "wolverhampton wanderers": "Wolves",
}

# (norm_display_name, norm_canonical_team) -> player_code
PLAYER_OVERRIDES: dict[tuple[str, str], str] = {
    ("rubenvinagre", "wolves"): "2020-21-player-471",       # Rúben Vinagre — full_name too long
    ("pablohernandez", "leeds"): "2020-21-player-193",      # Pablo Hernández Domínguez — norm mismatch
    ("edisoncavani", "manutd"): "2020-21-player-569",       # "Edinson" vs "Edison" typo in Excel
    ("sebastianhaller", "westham"): "2020-21-player-441",   # Sébastien vs Sebastian
    ("oluwasemilogoajayi", "westbrom"): "2020-21-player-419",  # full_name much longer
    ("brunofernandes", "manutd"): "2020-21-player-302",     # full_name much longer
    ("oxladechamberlain", "liverpool"): "2020-21-player-248",  # no first name in Excel
    ("danielceballos", "arsenal"): "2020-21-player-501",    # full_name has Fernández suffix
    ("abdoulaydoucoure", "everton"): "2020-21-player-512",  # Doucouré accent safety
    ("delealli", "spurs"): "2020-21-player-394",            # Bamidele Alli, friendly="Alli"
    ("gabrieljesus", "mancity"): "2020-21-player-282",      # full_name has middle names
    ("ricardopereira", "leicester"): "2020-21-player-226",  # full_name has middle names
}

# Mid-season swaps: {manager_name: [(original_code, excel_replacement_code, gw_switch)]}
# The Excel already shows the replacement player. We adjust its gw_from and add the
# original player as a GW1→(gw_switch-1) row.
MID_SEASON_SWAPS: dict[str, list[tuple[str, str, int]]] = {
    "Andy Fowkes": [
        ("2020-21-player-441", "2020-21-player-569", 16),  # Haller GW1-15, Cavani GW16-38
    ],
}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return "".join(c for c in s.lower() if c.isalpha())


def normalize_team(name: str) -> str:
    return TEAM_ALIASES.get(name.strip().lower(), name.strip())


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    players_df = pd.read_csv(DATA_DIR / "players.csv", dtype=str).fillna("")
    season_p = players_df[players_df["season"] == SEASON_ID]
    outfield = season_p[season_p["type"] == "player"]
    teams_df = season_p[season_p["type"] == "team"]

    team_id_lookup: dict[str, str] = {row["full_name"]: row["element_id"] for _, row in teams_df.iterrows()}
    tid_to_name: dict[str, str] = {row["element_id"]: row["full_name"] for _, row in teams_df.iterrows()}

    full_lookup: dict[tuple[str, str], str] = {}
    friendly_lookup: dict[tuple[str, str], str] = {}
    all_full: dict[str, list[str]] = {}
    all_friendly: dict[str, list[str]] = {}

    for _, row in outfield.iterrows():
        tname = tid_to_name.get(row["team_id"], "")
        nt = _norm(tname)
        fn = _norm(row["full_name"])
        wb_n = _norm(row["friendly_name"])
        full_lookup[(fn, nt)] = row["code"]
        friendly_lookup[(wb_n, nt)] = row["code"]
        parts = row["friendly_name"].split()
        if len(parts) > 1:
            friendly_lookup[(_norm(parts[-1]), nt)] = row["code"]
        all_full.setdefault(fn, []).append(row["code"])
        all_friendly.setdefault(wb_n, []).append(row["code"])

    wb = xlrd.open_workbook(XLS_PATH)
    all_rows: list[dict] = []
    unmatched: list[str] = []
    POSITION_ORDER = ["GK", "DEF", "DEF", "DEF", "MID", "MID", "MID", "MID", "MID", "FWD", "FWD"]  # 3-5-2

    for manager in MANAGER_SHEETS:
        sheet_name = SHEET_NAME_OVERRIDES.get(manager, manager)
        if sheet_name not in wb.sheet_names():
            print(f"WARN: sheet '{sheet_name}' not found")
            continue

        ws = wb.sheet_by_name(sheet_name)
        print(f"\n=== {manager} ===")

        manager_rows: list[dict] = []
        for row_idx, position in enumerate(POSITION_ORDER):
            raw_row = ws.row_values(7 + row_idx)  # rows 8-18 (1-indexed) = 7-17 (0-indexed)
            col_a = str(raw_row[0]).strip() if raw_row[0] else ""
            col_b = str(raw_row[1]).strip() if raw_row[1] else ""
            col_c = raw_row[2]
            cost = str(int(col_c)) if isinstance(col_c, float) else str(col_c or "")

            if position == "GK":
                canonical = normalize_team(col_a)
                team_id = team_id_lookup.get(canonical, "")
                if not team_id:
                    print(f"  UNMATCHED GK: '{col_a}' -> '{canonical}'")
                    unmatched.append(f"{manager} | GK | {col_a}")
                    continue
                player_code = f"{SEASON_ID}-team-{team_id}"
                print(f"  GK  {col_a:28s} -> {player_code}  {cost}")
            else:
                canonical_team = normalize_team(col_b)
                norm_t = _norm(canonical_team)
                norm_full = _norm(col_a)

                player_code = (
                    PLAYER_OVERRIDES.get((norm_full, norm_t), "")
                    or full_lookup.get((norm_full, norm_t), "")
                    or friendly_lookup.get((norm_full, norm_t), "")
                )
                if not player_code:
                    hits = all_full.get(norm_full, [])
                    if len(hits) == 1:
                        player_code = hits[0]
                if not player_code:
                    hits = all_friendly.get(norm_full, [])
                    if len(hits) == 1:
                        player_code = hits[0]

                if not player_code:
                    print(f"  UNMATCHED {position}: '{col_a}' @ '{col_b}'")
                    unmatched.append(f"{manager} | {position} | {col_a} @ {col_b}")
                    continue

                print(f"  {position}  {col_a:28s} {col_b:16s} -> {player_code}  {cost}")

            manager_rows.append({
                "player_code": player_code,
                "season_id": SEASON_ID,
                "manager_name": manager,
                "position": position,
                "cost": cost,
                "gw_from": "1",
                "gw_to": "38",
            })

        # Apply mid-season swaps: Excel shows replacement; add original as early-GW row
        for orig_code, repl_code, gw_switch in MID_SEASON_SWAPS.get(manager, []):
            for row in manager_rows:
                if row["player_code"] == repl_code:
                    row["gw_from"] = str(gw_switch)
                    repl_pos = row["position"]
                    repl_cost = row["cost"]
                    print(f"  SWAP {orig_code} GW1-{gw_switch - 1} / {repl_code} GW{gw_switch}-38")
                    manager_rows.append({
                        "player_code": orig_code,
                        "season_id": SEASON_ID,
                        "manager_name": manager,
                        "position": repl_pos,
                        "cost": repl_cost,
                        "gw_from": "1",
                        "gw_to": str(gw_switch - 1),
                    })
                    break

        all_rows.extend(manager_rows)

    print(f"\n{'='*60}")
    print(f"Matched:   {len(all_rows)}")
    print(f"Unmatched: {len(unmatched)}")
    if unmatched:
        print("\nUnmatched:")
        for u in unmatched:
            print(f"  {u}")

    if not args.dry_run and all_rows:
        new_df = pd.DataFrame(all_rows)
        sel_path = DATA_DIR / "manager_selections.csv"
        if sel_path.exists() and sel_path.stat().st_size > 0:
            existing = pd.read_csv(sel_path, dtype=str).fillna("")
            existing = existing[existing["season_id"] != SEASON_ID]
            result = pd.concat([existing, new_df], ignore_index=True)
        else:
            result = new_df
        result.to_csv(sel_path, index=False)
        print(f"\nWritten {len(new_df)} rows to manager_selections.csv")
    elif args.dry_run:
        print("\nDry run - no changes written.")


if __name__ == "__main__":
    main()
