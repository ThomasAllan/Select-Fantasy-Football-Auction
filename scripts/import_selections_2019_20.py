"""
Import 2019-20 manager selections from FOOTY2019.xls into manager_selections.csv.

Format: "I.Surname - Team" in column A, cost in column B.
Andy Fowkes did not participate this season; Gary Evans joined.
Tom Allan participated (sheet named "Tom Allan").

Usage:
    uv run python scripts/import_selections_2019_20.py [--dry-run]
"""

import argparse
import sys
import unicodedata
from pathlib import Path

import xlrd
import pandas as pd

DATA_DIR = Path("data")
XLS_PATH = Path("historic_selections/FOOTY2019.xls")
SEASON_ID = "2019-20"

MANAGER_SHEETS = [
    "Thomas Allan", "Karl Allen", "Jamie Blunt", "Andrea Chapman", "Gary Evans",
    "Tom Fowkes", "Steve Gale", "Pam Hart", "Tim Hart", "Mick Jones", "Ken Maggs",
    "Gary Speechley", "Kev Thulbourne", "Wendy Thulbourne", "Jamie Wright", "Neil Wright",
    # Andy Fowkes did not play this season
]

SHEET_NAME_OVERRIDES = {"Thomas Allan": "Tom Allan"}

# 2019-20 formation: 1 GK + 3 DEF + 4 MID + 3 FWD = 11 players
# (user described this as "3-5-3" but the Excel shows 4 MID rows, making it 3-4-3)
POSITION_ORDER = ["GK", "DEF", "DEF", "DEF", "MID", "MID", "MID", "MID", "FWD", "FWD", "FWD"]

TEAM_ALIASES: dict[str, str] = {
    "arsenal": "Arsenal",
    "aston villa": "Aston Villa",
    "bournemouth": "Bournemouth",
    "brighton ha": "Brighton", "brighton": "Brighton",
    "burnley": "Burnley",
    "c.palace": "Crystal Palace", "crystal palace": "Crystal Palace",
    "chelsea": "Chelsea",
    "everton": "Everton",
    "leicester": "Leicester", "leicester city": "Leicester",
    "liverpool": "Liverpool",
    "man city": "Man City", "manchester city": "Man City",
    "man utd": "Man Utd", "man united": "Man Utd", "manchester united": "Man Utd",
    "newastle utd": "Newcastle",  # Steve Gale typo
    "newcastle": "Newcastle", "newcastle united": "Newcastle", "newcastle utd": "Newcastle",
    "norwich": "Norwich", "norwich city": "Norwich",
    "sheff utd": "Sheffield Utd", "sheff. utd": "Sheffield Utd",
    "sheffield utd": "Sheffield Utd", "sheffield united": "Sheffield Utd",
    "southampton": "Southampton",
    "spurs": "Spurs", "tottenham": "Spurs", "tottenham hotspur": "Spurs",
    "watford": "Watford", "watford town": "Watford",
    "west ham": "West Ham", "west ham united": "West Ham",
    "wolves": "Wolves", "wolverhampton wanderers": "Wolves",
}

# (norm_of_player_string, norm_canonical_team) -> player_code
# Two key forms are used:
#   - norm of full player string (e.g. "bsilva") for disambiguation (B.Silva vs D.Silva)
#   - norm of extracted surname (e.g. "schlup") for typo/mismatch corrections
PLAYER_OVERRIDES: dict[tuple[str, str], str] = {
    # Surname typos / mismatches
    ("schlup", "crystalpalace"): "2019-20-player-137",       # J.Schlup → Schlupp (missing p)
    ("vaanholt", "crystalpalace"): "2019-20-player-123",     # P.V.Aanholt → extract gives "V.Aanholt"
    ("heungming", "spurs"): "2019-20-player-342",            # Son. Heung-Ming → friendly="Son"
    ("stmaximin", "newcastle"): "2019-20-player-500",        # A.St-Maximin → friendly="Saint-Maximin"
    ("deondoncker", "wolves"): "2019-20-player-420",         # L.Deondoncker → Dendoncker (extra o)
    ("vinegre", "wolves"): "2019-20-player-406",             # R.Vinegre → Vinagre (typo)
    ("oxlaidechamberlain", "liverpool"): "2019-20-player-193",  # A.Oxlaide-Chamberlain (typo + friendly="Chamberlain")
    ("ghazi", "astonvilla"): "2019-20-player-30",            # AE.Ghazi → friendly="El Ghazi"
    ("celso", "spurs"): "2019-20-player-523",                # GL.Celso → friendly="Lo Celso"
    # Disambiguation: full player string (initial+surname) needed when surname alone is ambiguous
    ("bsilva", "mancity"): "2019-20-player-218",             # B.Silva = Bernardo Silva
    ("dsilva", "mancity"): "2019-20-player-219",             # D.Silva = David Silva
    ("cwilson", "bournemouth"): "2019-20-player-67",         # C.Wilson = Callum Wilson (not Harry)
    ("hwilson", "bournemouth"): "2019-20-player-505",        # H.Wilson = Harry Wilson
    # Surname typo not caught above
    ("jiminez", "wolves"): "2019-20-player-409",             # R.Jiminez → Jiménez
}

# Mid-season swaps: {manager_name: [(original_code, excel_replacement_code, gw_switch)]}
# Excel shows the replacement player; we adjust gw_from and add the original as a GW1→(gw_switch-1) row.
MID_SEASON_SWAPS: dict[str, list[tuple[str, str, int]]] = {
    "Pam Hart": [
        ("2019-20-player-343", "2019-20-player-618", 24),  # Eriksen GW1-23, Fernandes GW24-38
    ],
    "Mick Jones": [
        ("2019-20-player-386", "2019-20-player-468", 4),   # Hernandez GW1-3, Ayew GW4-38
    ],
    "Ken Maggs": [
        ("2019-20-player-510", "2019-20-player-493", 22),  # Camarasa GW1-21, Webster GW22-38
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
                print(f"  GK  {col_a:30s} -> {player_code}  {cost}")
            else:
                # Parse "I.Surname - Team" from col_a
                parts = col_a.rsplit(" - ", 1)
                if len(parts) == 2:
                    player_str, team_str = parts[0].strip(), parts[1].strip()
                else:
                    player_str, team_str = col_a, ""

                canonical_team = normalize_team(team_str)
                norm_t = _norm(canonical_team)
                norm_full = _norm(player_str)           # e.g. "bsilva"
                norm_sur = _norm(extract_surname(player_str))  # e.g. "silva"

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

                print(f"  {position}  {col_a:35s} -> {player_code}  {cost}")

            manager_rows.append({
                "player_code": player_code,
                "season_id": SEASON_ID,
                "manager_name": manager,
                "position": position,
                "cost": cost,
                "gw_from": "1",
                "gw_to": "47",  # 2019-20 used GW1-29 then GW39-47 after COVID restart
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
