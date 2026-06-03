"""
Import 2018-19 manager selections from FOOTY2018.xls into manager_selections.csv.

Format: "I.Surname - Team" in column A, cost in column B.
Mark Forth joined this season. Jamie Blunt, Andy Fowkes, Niall McLoughlin absent.
Tom Allan participated (sheet named "Tom Allan").

Usage:
    uv run python scripts/import_selections_2018_19.py [--dry-run]
"""

import argparse
import sys
import unicodedata
from pathlib import Path

import xlrd
import pandas as pd

from manager_names import canonicalize

DATA_DIR = Path("data")
XLS_PATH = Path("historic_selections/FOOTY2018.xls")
SEASON_ID = "2018-19"

MANAGER_SHEETS = [
    "Thomas Allan", "Karl Allen", "Andrea Chapman", "Gary Evans", "Mark Forth",
    "Tom Fowkes", "Steve Gale", "Pam Hart", "Tim Hart", "Mick Jones", "Ken Maggs",
    "Gary Speechley", "Kev Thulbourne", "Wendy Thulbourne", "Jamie Wright", "Neil Wright",
]

SHEET_NAME_OVERRIDES = {"Thomas Allan": "Tom Allan"}

# 2018-19 formation: 1 GK + 3 DEF + 4 MID + 3 FWD = 11 players (3-4-3)
POSITION_ORDER = ["GK", "DEF", "DEF", "DEF", "MID", "MID", "MID", "MID", "FWD", "FWD", "FWD"]

TEAM_ALIASES: dict[str, str] = {
    "arsenal": "Arsenal",
    "aston villa": "Aston Villa",
    "bournemouth": "Bournemouth",
    "brighton hove albion": "Brighton", "brighton ha": "Brighton", "brighton": "Brighton",
    "burnley": "Burnley",
    "c.palace": "Crystal Palace", "crystal palace": "Crystal Palace",
    "cardiff city": "Cardiff City", "cardiff": "Cardiff City",
    "chelsea": "Chelsea",
    "everton": "Everton",
    "fulham": "Fulham",
    "huddersfield town": "Huddersfield", "hudderfield town": "Huddersfield",  # typo fix
    "huddersfield": "Huddersfield",
    "leicester city": "Leicester", "leicester": "Leicester",
    "liverpool": "Liverpool",
    "man city": "Man City", "manchester city": "Man City",
    "man utd": "Man Utd", "man united": "Man Utd", "manchester united": "Man Utd",
    "newcastle": "Newcastle", "newcastle united": "Newcastle", "newcastle utd": "Newcastle",
    "sheffield utd": "Sheffield Utd", "sheffield united": "Sheffield Utd",
    "southampton": "Southampton",
    "spurs": "Spurs", "tottenham": "Spurs", "tottenham hotspur": "Spurs",
    "watford": "Watford", "watford town": "Watford",
    "west ham": "West Ham", "west ham united": "West Ham", "west ham utd": "West Ham",
    "wolverhampton wanderers": "Wolves", "wolves": "Wolves",
}

# (norm_of_player_string, norm_canonical_team) -> player_code
PLAYER_OVERRIDES: dict[tuple[str, str], str] = {
    # Name mismatches / typos
    ("heungming", "spurs"): "2018-19-player-367",          # Son.Heung-Ming → fn="Son"
    ("papastathopoulos", "arsenal"): "2018-19-player-12",  # S.Papastathopoulos → fn="Sokratis"
    ("oxlaidechamberlain", "liverpool"): "2018-19-player-248",  # typo vs Oxlade-Chamberlain
    ("alexander", "liverpool"): "2018-19-player-245",      # T.Alexander → Alexander-Arnold
    ("lichtensteiner", "arsenal"): "2018-19-player-11",    # extra 'n': Lichtensteiner→Lichtsteiner
    ("pereryra", "watford"): "2018-19-player-391",          # R.Pereryra → Pereyra
    ("wilshire", "westham"): "2018-19-player-447",          # Wilshire → Wilshere
    ("kolasnic", "arsenal"): "2018-19-player-8",            # Kolasnic → Kolasinac
    ("shaquiri", "liverpool"): "2018-19-player-462",        # Shaquiri → Shaqiri
    ("wellbeck", "arsenal"): "2018-19-player-21",           # Wellbeck → Welbeck
    # Disambiguation: full player string (initial+surname)
    ("bsilva", "mancity"): "2018-19-player-276",            # B.Silva = Bernardo Silva (Man City)
    ("dsilva", "mancity"): "2018-19-player-271",            # D.Silva = David Silva
    ("lcook", "bournemouth"): "2018-19-player-39",          # L.Cook = Lewis Cook (not Steve)
    ("rsessegnon", "fulham"): "2018-19-player-184",         # R.Sessegnon = Ryan (not Steven)
    ("gross", "brighton"): "2018-19-player-59",             # P.Gross → Groß (ß not matched by norm)
    ("tau", "brighton"): "2018-19-player-625",              # P.Tau — on loan abroad, 0 pts
    ("hernandez", "westham"): "2018-19-player-419",         # J.Hernandez = Chicharito (fn="Chicharito")
}

MID_SEASON_SWAPS: dict[str, list[tuple[str, str, int]]] = {
    "Andrea Chapman": [
        ("2018-19-player-298", "2018-19-player-42", 23),   # Fellaini GW1-22, Brooks GW23-38
    ],
    "Ken Maggs": [
        ("2018-19-player-123", "2018-19-player-391", 22),  # Fabregas GW1-21, Pereyra GW22-38
        ("2018-19-player-84", "2018-19-player-437", 23),   # Vokes GW1-22, Jimenez GW23-38
    ],
}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return "".join(c for c in s.lower() if c.isalpha())


def normalize_team(name: str) -> str:
    return TEAM_ALIASES.get(name.strip().lower(), name.strip())


def extract_surname(player_str: str) -> str:
    """Strip 'X.' or 'XY.' prefix; if no dot, return whole string."""
    s = player_str.strip()
    if "." in s:
        return s.split(".", 1)[1].strip()
    return s


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

    friendly_lookup: dict[tuple[str, str], str] = {}
    surname_all: dict[str, list[str]] = {}

    for _, row in outfield.iterrows():
        tname = tid_to_name.get(row["team_id"], "")
        nt = _norm(tname)
        fn = _norm(row["friendly_name"])
        friendly_lookup[(fn, nt)] = row["code"]
        parts = row["friendly_name"].split()
        if len(parts) > 1:
            friendly_lookup[(_norm(parts[-1]), nt)] = row["code"]
        surname_all.setdefault(fn, []).append(row["code"])

    wb = xlrd.open_workbook(XLS_PATH)
    all_rows: list[dict] = []
    unmatched: list[str] = []

    for manager in MANAGER_SHEETS:
        sheet_name = SHEET_NAME_OVERRIDES.get(manager, manager)
        if sheet_name not in wb.sheet_names():
            print(f"WARN: sheet '{sheet_name}' not found")
            continue

        ws = wb.sheet_by_name(sheet_name)
        print(f"\n=== {manager} ===")

        manager_rows: list[dict] = []
        for row_idx, position in enumerate(POSITION_ORDER):
            raw_row = ws.row_values(7 + row_idx)
            col_a = str(raw_row[0]).strip() if raw_row[0] else ""
            cost_val = raw_row[1]
            cost = str(int(cost_val)) if isinstance(cost_val, float) else str(cost_val or "")

            if position == "GK":
                canonical = normalize_team(col_a)
                team_id = team_id_lookup.get(canonical, "")
                if not team_id:
                    print(f"  UNMATCHED GK: '{col_a}' -> '{canonical}'")
                    unmatched.append(f"{manager} | GK | {col_a}")
                    continue
                player_code = f"{SEASON_ID}-team-{team_id}"
                print(f"  GK  {col_a:35s} -> {player_code}  {cost}")
            else:
                parts = col_a.rsplit(" - ", 1)
                if len(parts) == 2:
                    player_str, team_str = parts[0].strip(), parts[1].strip()
                else:
                    player_str, team_str = col_a, ""

                canonical_team = normalize_team(team_str)
                norm_t = _norm(canonical_team)
                norm_full = _norm(player_str)
                norm_sur = _norm(extract_surname(player_str))

                player_code = (
                    PLAYER_OVERRIDES.get((norm_full, norm_t), "")
                    or PLAYER_OVERRIDES.get((norm_sur, norm_t), "")
                    or friendly_lookup.get((norm_sur, norm_t), "")
                )
                if not player_code:
                    hits = surname_all.get(norm_sur, [])
                    if len(hits) == 1:
                        player_code = hits[0]

                if not player_code:
                    print(f"  UNMATCHED {position}: '{col_a}'")
                    unmatched.append(f"{manager} | {position} | {col_a}")
                    continue

                print(f"  {position}  {col_a:38s} -> {player_code}  {cost}")

            manager_rows.append({
                "player_code": player_code,
                "season_id": SEASON_ID,
                "manager_name": canonicalize(manager),
                "position": position,
                "cost": cost,
                "gw_from": "1",
                "gw_to": "38",
            })

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
                        "manager_name": canonicalize(manager),
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
