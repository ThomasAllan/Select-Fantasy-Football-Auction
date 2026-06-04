"""
Import manager selections from a FOOTY20XX.xlsx workbook for any season.

Designed for ongoing use at the start of each new season. Run sync-scores first
so that players.csv is populated for the new season, then run this script.

USAGE
-----
    uv run python scripts/import_season.py FOOTY2026.xlsx
    uv run python scripts/import_season.py FOOTY2026.xlsx --season 2026-27
    uv run python scripts/import_season.py FOOTY2026.xlsx --dry-run
    uv run python scripts/import_season.py FOOTY2026.xlsx --overrides fixes.csv

EXCEL WORKBOOK LAYOUT (one sheet per manager)
---------------------------------------------
  Row 8:      GK team  (col A = team name,       col C = cost)
  Rows 9-11:  DEF      (col A = I.Surname,       col B = club, col C = cost)
  Rows 12-15: MID      (same)
  Rows 16-18: FWD      (same)

Cost can be a number (13), a float (13.0), or a string with £ prefix (£13).

WHEN PLAYERS DON'T MATCH
-------------------------
The script prints all unmatched rows and generates an overrides CSV template.
Copy the template, fill in the player_code column from players.csv, save as
(e.g.) fixes.csv, and re-run with --overrides fixes.csv.

    excel_name,excel_team,player_code
    B.Hojlund,Man Utd,2026-27-player-123
    Arsenal,,2026-27-team-1

For GK teams, leave excel_team blank. To look up player_codes, open
data/players.csv and filter by season and full_name.

SHEET NAME ALIASES
------------------
If a manager's sheet name differs from their canonical name (e.g. "Tom Allan"
vs "Thomas Allan"), add an entry to scripts/manager_names.py under
CANONICAL_NAMES. The script uses that mapping automatically.

SEASON AUTO-DETECTION
---------------------
Without --season, the script picks the most recent season that has player data
in players.csv. Run 'uv run sync-scores' first to populate it.
"""

import argparse
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import openpyxl
import pandas as pd

from manager_names import canonicalize

DATA_DIR = Path("data")
SCRIPTS_DIR = Path(__file__).parent

POSITION_ORDER = ["GK", "DEF", "DEF", "DEF", "MID", "MID", "MID", "MID", "FWD", "FWD", "FWD"]

# Sheet names that are not manager sheets — skip these
SKIP_SHEETS = {
    "summary", "rules", "prizes", "totals", "points", "league", "league table",
    "leaderboard", "other", "template", "example", "instructions",
    "sheet1", "sheet2", "sheet3", "overview", "scores", "standings", "history",
}

# Comprehensive team aliases: any spelling a manager might use → canonical FPL full_name.
# The canonical name is what appears in players.csv full_name for type=team.
# Add new entries here when new teams are promoted to the Premier League.
TEAM_ALIASES: dict[str, str] = {
    # Arsenal
    "arsenal": "Arsenal",
    # Aston Villa
    "aston villa": "Aston Villa",
    "villa": "Aston Villa",
    "a.villa": "Aston Villa",
    "avfc": "Aston Villa",
    # Bournemouth
    "bournemouth": "Bournemouth",
    "afc bournemouth": "Bournemouth",
    "afcb": "Bournemouth",
    # Brentford
    "brentford": "Brentford",
    "brentord": "Brentford",  # common typo
    # Brighton
    "brighton": "Brighton",
    "brighton & hove albion": "Brighton",
    "brighton and hove albion": "Brighton",
    "brighton ha": "Brighton",
    "brighton hove albion": "Brighton",
    "bha": "Brighton",
    # Burnley
    "burnley": "Burnley",
    # Chelsea
    "chelsea": "Chelsea",
    # Crystal Palace
    "crystal palace": "Crystal Palace",
    "c.palace": "Crystal Palace",
    "cpfc": "Crystal Palace",
    "palace": "Crystal Palace",
    # Everton
    "everton": "Everton",
    # Fulham
    "fulham": "Fulham",
    # Ipswich
    "ipswich": "Ipswich",
    "ipswich town": "Ipswich",
    # Leeds
    "leeds": "Leeds",
    "leeds united": "Leeds",
    "lufc": "Leeds",
    # Leicester
    "leicester": "Leicester",
    "leicester city": "Leicester",
    "lcfc": "Leicester",
    # Liverpool
    "liverpool": "Liverpool",
    "lfc": "Liverpool",
    # Luton
    "luton": "Luton",
    "luton town": "Luton",
    # Man City
    "man city": "Man City",
    "man. city": "Man City",
    "manchester city": "Man City",
    "mancity": "Man City",
    "mcfc": "Man City",
    # Man Utd
    "man utd": "Man Utd",
    "man united": "Man Utd",
    "man. utd": "Man Utd",
    "manchester united": "Man Utd",
    "manchester utd": "Man Utd",
    "manutd": "Man Utd",
    "mufc": "Man Utd",
    # Middlesbrough
    "middlesbrough": "Middlesbrough",
    "boro": "Middlesbrough",
    # Newcastle
    "newcastle": "Newcastle",
    "newcastle united": "Newcastle",
    "newcastle utd": "Newcastle",
    "nufc": "Newcastle",
    # Norwich
    "norwich": "Norwich",
    "norwich city": "Norwich",
    "ncfc": "Norwich",
    # Nott'm Forest
    "nottingham forest": "Nott'm Forest",
    "notts forest": "Nott'm Forest",
    "nott'm forest": "Nott'm Forest",
    "nottm forest": "Nott'm Forest",
    "nffc": "Nott'm Forest",
    "forest": "Nott'm Forest",
    # Sheffield Utd
    "sheffield utd": "Sheffield Utd",
    "sheffield united": "Sheffield Utd",
    "sheffield": "Sheffield Utd",
    "sufc": "Sheffield Utd",
    # Sunderland
    "sunderland": "Sunderland",
    "safc": "Sunderland",
    # Southampton
    "southampton": "Southampton",
    "saints": "Southampton",
    "scfc": "Southampton",
    # Spurs / Tottenham
    "spurs": "Spurs",
    "tottenham": "Spurs",
    "tottenham hotspur": "Spurs",
    "thfc": "Spurs",
    # Swansea
    "swansea": "Swansea",
    "swansea city": "Swansea",
    "scfc": "Swansea",
    # Watford
    "watford": "Watford",
    "wfc": "Watford",
    # West Brom
    "west brom": "West Brom",
    "west bromwich albion": "West Brom",
    "wba": "West Brom",
    # West Ham
    "west ham": "West Ham",
    "west ham united": "West Ham",
    "west ham utd": "West Ham",
    "hammers": "West Ham",
    "whufc": "West Ham",
    # Wolves
    "wolves": "Wolves",
    "wolverhampton wanderers": "Wolves",
    "wolverhampton": "Wolves",
    "wwfc": "Wolves",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    """Lowercase, strip diacritics (é→e, ø→o, etc.), remove non-alpha chars."""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return "".join(c for c in s.lower() if c.isalpha())


def _clean_cost(val: object) -> str:
    if isinstance(val, (int, float)):
        return str(int(val))
    cleaned = re.sub(r"[£$,\s]", "", str(val or ""))
    try:
        return str(int(float(cleaned)))
    except ValueError:
        return "0"


def _extract_surname(excel_name: str) -> str:
    """
    Extract the surname from an Excel player name.
    'B.Saka' -> 'Saka'
    'K.De Bruyne' -> 'De Bruyne'
    'Haaland' -> 'Haaland'
    """
    name = excel_name.strip()
    if "." in name:
        return name.split(".", 1)[1].strip()
    return name


def _normalize_team(name: str) -> str | None:
    """Return canonical FPL full_name for a team alias, or None if unknown."""
    return TEAM_ALIASES.get(name.strip().lower())


# ---------------------------------------------------------------------------
# Build lookups from players.csv
# ---------------------------------------------------------------------------

def _build_lookups(players_df: pd.DataFrame, season_id: str) -> tuple[
    dict[str, str],              # team full_name -> element_id
    dict[str, str],              # team element_id -> full_name
    dict[tuple[str, str], str],  # (norm_name_variant, norm_team) -> player_code
    dict[str, list[str]],        # norm_friendly_name -> [player_codes]
]:
    season_p = players_df[players_df["season"] == season_id]
    teams_df = season_p[season_p["type"] == "team"]
    outfield = season_p[season_p["type"] == "player"]

    team_id_lookup: dict[str, str] = {}
    tid_to_name: dict[str, str] = {}
    for _, r in teams_df.iterrows():
        eid = str(r["element_id"])
        fname = str(r["full_name"])
        team_id_lookup[fname] = eid
        tid_to_name[eid] = fname

    player_lookup: dict[tuple[str, str], str] = {}
    surname_all: dict[str, list[str]] = {}

    for _, r in outfield.iterrows():
        tname = tid_to_name.get(str(r.get("team_id", "")), "")
        nt = _norm(tname)
        code = str(r["code"])
        fn = str(r.get("friendly_name", "") or "")
        full = str(r.get("full_name", "") or "")

        # Index by full FPL friendly_name (normalised)
        norm_fn = _norm(fn)
        player_lookup[(norm_fn, nt)] = code

        # If FPL uses "X.Surname" format, also index just the surname part
        if "." in fn:
            suffix = fn.split(".", 1)[1].strip()
            player_lookup[(_norm(suffix), nt)] = code

        # Last word of multi-word friendly_name (e.g. "Van de Ven" → "ven")
        parts = fn.split()
        if len(parts) > 1:
            player_lookup[(_norm(parts[-1]), nt)] = code
            # First word too (e.g. "Diogo J." → "diogo" helps match "D.Jota")
            player_lookup[(_norm(parts[0]), nt)] = code

        # FPL full_name last word (e.g. full_name="Darwin Núñez" → "nunez")
        fparts = full.split()
        if fparts:
            player_lookup[(_norm(fparts[-1]), nt)] = code
            if len(fparts) > 1:
                player_lookup[(_norm(fparts[0]), nt)] = code

        # surname_all: for unique-across-season fallback
        surname_all.setdefault(norm_fn, []).append(code)

    return team_id_lookup, tid_to_name, player_lookup, surname_all


# ---------------------------------------------------------------------------
# Player matching
# ---------------------------------------------------------------------------

def _match_player(
    excel_name: str,
    excel_team: str,
    player_lookup: dict[tuple[str, str], str],
    surname_all: dict[str, list[str]],
    overrides: dict[tuple[str, str], str],
) -> tuple[str, str]:
    """
    Try to find a player_code for an Excel name+team combination.
    Returns (player_code, method_description).
    Returns ("", "") if no match found.
    """
    surname = _extract_surname(excel_name)
    canonical_team = _normalize_team(excel_team) or excel_team.strip()
    norm_surname = _norm(surname)
    norm_team = _norm(canonical_team)

    # 1. Overrides file (highest priority — exact match on raw Excel values)
    ov_key = (_norm(excel_name), _norm(excel_team))
    if ov_key in overrides and overrides[ov_key]:
        return overrides[ov_key], "override"

    # 2. (surname, team) — the main matching path
    code = player_lookup.get((norm_surname, norm_team), "")
    if code:
        return code, "surname+team"

    # 3. Full Excel name (before initial stripping) + team
    code = player_lookup.get((_norm(excel_name.strip()), norm_team), "")
    if code:
        return code, "fullname+team"

    # 4. Unique surname anywhere across the whole season (ignores team)
    matches = surname_all.get(norm_surname, [])
    if len(matches) == 1:
        return matches[0], "unique surname"

    return "", ""


def _resolve_gk_team(
    excel_name: str,
    team_id_lookup: dict[str, str],
    season_id: str,
    overrides: dict[tuple[str, str], str],
) -> tuple[str, str]:
    """
    Match GK team name to a player_code.
    Returns (player_code, method_description) or ("", "") if unmatched.
    """
    # Override: key = (norm_excel_name, "")
    ov_key = (_norm(excel_name), "")
    if ov_key in overrides and overrides[ov_key]:
        return overrides[ov_key], "override"

    canonical = _normalize_team(excel_name) or excel_name.strip()
    team_id = team_id_lookup.get(canonical, "")
    if team_id:
        return f"{season_id}-team-{team_id}", "team alias"

    # Try raw name directly against full_name lookup
    team_id = team_id_lookup.get(excel_name.strip(), "")
    if team_id:
        return f"{season_id}-team-{team_id}", "exact team name"

    return "", ""


# ---------------------------------------------------------------------------
# Config loaders
# ---------------------------------------------------------------------------


def _load_overrides(overrides_path: str | None) -> dict[tuple[str, str], str]:
    """
    Load (norm_excel_name, norm_excel_team) -> player_code from a CSV file.
    For GK teams, excel_team should be blank in the CSV.
    """
    if not overrides_path:
        return {}
    path = Path(overrides_path)
    if not path.exists():
        print(f"ERROR: overrides file not found: {path}")
        sys.exit(1)
    df = pd.read_csv(path, dtype=str).fillna("")
    result: dict[tuple[str, str], str] = {}
    for _, r in df.iterrows():
        key = (_norm(str(r.get("excel_name", ""))), _norm(str(r.get("excel_team", ""))))
        code = str(r.get("player_code", "")).strip()
        if code:
            result[key] = code
    return result


def _detect_season(players_df: pd.DataFrame) -> str:
    """Return the most recent season in players.csv by lexicographic sort."""
    seasons = [s for s in players_df["season"].unique() if s]
    if not seasons:
        print("ERROR: players.csv is empty. Run 'uv run sync-scores' first.")
        sys.exit(1)
    return sorted(seasons)[-1]


# ---------------------------------------------------------------------------
# Main import logic
# ---------------------------------------------------------------------------

def _import_workbook(
    xlsx_path: Path,
    season_id: str,
    players_df: pd.DataFrame,
    overrides: dict[tuple[str, str], str],
    dry_run: bool,
) -> None:
    team_id_lookup, _tid_to_name, player_lookup, surname_all = _build_lookups(players_df, season_id)

    if not team_id_lookup:
        print(f"ERROR: no team data for season {season_id} in players.csv.")
        print("Run 'uv run sync-scores' to populate it, then retry.")
        sys.exit(1)

    wb = openpyxl.load_workbook(xlsx_path, data_only=True)

    all_rows: list[dict] = []
    unmatched: list[dict] = []

    for sheet_name in wb.sheetnames:
        if sheet_name.strip().lower() in SKIP_SHEETS:
            continue

        manager = canonicalize(sheet_name)
        ws = wb[sheet_name]
        sheet_rows = list(ws.iter_rows(min_row=8, max_row=18, values_only=True))

        if len(sheet_rows) < 11:
            print(f"WARN: sheet '{sheet_name}' only has {len(sheet_rows)} data rows (expected 11) — skipping")
            continue

        print(f"\n=== {manager} ===")

        for raw_row, position in zip(sheet_rows[:11], POSITION_ORDER):
            col_a = str(raw_row[0] or "").strip()
            col_b = str(raw_row[1] or "").strip() if len(raw_row) > 1 else ""
            col_c = raw_row[2] if len(raw_row) > 2 else None
            cost = _clean_cost(col_c)

            if not col_a:
                print(f"  SKIP  [{position}] empty cell")
                continue

            if position == "GK":
                player_code, method = _resolve_gk_team(col_a, team_id_lookup, season_id, overrides)
                if player_code:
                    print(f"  GK    {col_a:<25s} -> {player_code}  £{cost}  [{method}]")
                else:
                    print(f"  UNMATCHED GK: '{col_a}' — not found in players.csv for {season_id}")
                    unmatched.append({"manager": manager, "position": position, "excel_name": col_a, "excel_team": ""})
                    continue
            else:
                player_code, method = _match_player(col_a, col_b, player_lookup, surname_all, overrides)
                if player_code:
                    print(f"  {position:<5s} {col_a:<25s} {col_b:<20s} -> {player_code}  £{cost}  [{method}]")
                else:
                    print(f"  UNMATCHED {position}: '{col_a}' @ '{col_b}'")
                    unmatched.append({"manager": manager, "position": position, "excel_name": col_a, "excel_team": col_b})
                    continue

            all_rows.append({
                "player_code": player_code,
                "season_id": season_id,
                "manager_name": manager,
                "position": position,
                "cost": cost,
                "gw_from": "1",
                "gw_to": "38",
            })

    # Summary
    print(f"\n{'=' * 60}")
    print(f"Season:    {season_id}")
    print(f"Matched:   {len(all_rows)}")
    print(f"Unmatched: {len(unmatched)}")

    if unmatched:
        print("\nUnmatched players:")
        print(f"  {'Manager':<20s} {'Pos':<5s} {'Excel name':<28s} {'Excel team'}")
        print(f"  {'-'*20} {'-'*5} {'-'*28} {'-'*20}")
        for u in unmatched:
            print(f"  {u['manager']:<20s} {u['position']:<5s} {u['excel_name']:<28s} {u['excel_team']}")

        print("\nOverrides CSV template — fill in player_code from data/players.csv and re-run with --overrides <file>:")
        print("excel_name,excel_team,player_code")
        for u in unmatched:
            print(f"{u['excel_name']},{u['excel_team']},")

    if not all_rows:
        print("\nNothing to write.")
        return

    if dry_run:
        print("\nDry run — no changes written.")
        return

    if unmatched:
        answer = input(f"\n{len(unmatched)} unmatched rows will be skipped. Write the {len(all_rows)} matched rows anyway? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted.")
            return

    new_df = pd.DataFrame(all_rows)
    sel_path = DATA_DIR / "manager_selections.csv"
    if sel_path.exists() and sel_path.stat().st_size > 0:
        existing = pd.read_csv(sel_path, dtype=str).fillna("")
        existing = existing[existing["season_id"] != season_id]
        result = pd.concat([existing, new_df], ignore_index=True)
    else:
        result = new_df
    result.to_csv(sel_path, index=False)
    print(f"\nWritten {len(new_df)} rows to {sel_path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import manager selections from a FOOTY20XX.xlsx workbook.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("xlsx", help="Path to the Excel workbook (e.g. FOOTY2026.xlsx)")
    parser.add_argument(
        "--season",
        help="Season ID to import into (e.g. 2026-27). Auto-detected from players.csv if omitted.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview matching only — no changes written to manager_selections.csv.",
    )
    parser.add_argument(
        "--overrides",
        metavar="FILE",
        help="Path to overrides CSV for manually resolving unmatched players.",
    )
    args = parser.parse_args()

    xlsx_path = Path(args.xlsx)
    if not xlsx_path.exists():
        print(f"ERROR: workbook not found: {xlsx_path}")
        sys.exit(1)

    players_csv = DATA_DIR / "players.csv"
    if not players_csv.exists():
        print(f"ERROR: {players_csv} not found. Run 'uv run sync-scores' first.")
        sys.exit(1)

    players_df = pd.read_csv(players_csv, dtype=str).fillna("")
    season_id = args.season or _detect_season(players_df)

    season_players = players_df[players_df["season"] == season_id]
    if season_players.empty:
        print(f"ERROR: no players found for season '{season_id}' in players.csv")
        print(f"Available seasons: {sorted(players_df['season'].unique())}")
        sys.exit(1)

    print(f"Season:   {season_id}")
    print(f"Workbook: {xlsx_path}")
    if args.dry_run:
        print("Mode:     dry-run (no writes)")
    if args.overrides:
        print(f"Overrides: {args.overrides}")

    overrides = _load_overrides(args.overrides)

    _import_workbook(
        xlsx_path=xlsx_path,
        season_id=season_id,
        players_df=players_df,
        overrides=overrides,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
