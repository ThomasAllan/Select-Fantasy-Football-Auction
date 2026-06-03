"""
Auto-generate data/player_aliases.yaml by matching synthetic historical player codes
(2009-10 through 2017-18) against players.csv (which covers 2018-19 onwards).

Matched entries map the synthetic code to the player's fpl_permanent_code.
Unmatched entries (retired before 2018) are written as YAML comments so they
can be filled in manually if needed.

Usage:
    uv run python scripts/generate_player_aliases.py [--dry-run]
"""

import argparse
import unicodedata
from pathlib import Path

import pandas as pd

DATA_DIR = Path("data")

HISTORICAL_SEASONS = {
    "2009-10", "2010-11", "2011-12", "2012-13", "2013-14",
    "2014-15", "2015-16", "2016-17", "2017-18",
}

# Match spreadsheet team text to normalised form for lookup
TEAM_ALIASES: dict[str, str] = {
    "arsenal": "arsenal",
    "aston villa": "astonvilla",
    "birmingham city": "birminghamcity", "birmingham": "birminghamcity",
    "blackburn": "blackburn", "blackburn rovers": "blackburn",
    "blackpool": "blackpool",
    "bolton": "bolton", "bolton wanderers": "bolton",
    "bournemouth": "bournemouth", "afc bournemouth": "bournemouth",
    "brighton": "brighton", "brighton & hove albion": "brighton",
    "burnley": "burnley",
    "cardiff city": "cardiffcity", "cardiff": "cardiffcity",
    "chelsea": "chelsea",
    "crystal palace": "crystalpalace", "c.palace": "crystalpalace",
    "derby county": "derbycounty", "derby": "derbycounty",
    "everton": "everton",
    "fulham": "fulham",
    "hull city": "hullcity", "hull": "hullcity",
    "huddersfield": "huddersfield", "huddersfield town": "huddersfield",
    "ipswich": "ipswich", "ipswich town": "ipswich",
    "leeds": "leedsutd", "leeds united": "leedsutd",
    "leicester": "leicester", "leicester city": "leicester",
    "liverpool": "liverpool",
    "man city": "mancity", "manchester city": "mancity",
    "man utd": "manutd", "man united": "manutd", "manchester united": "manutd",
    "middlesbrough": "middlesbrough",
    "newcastle": "newcastle", "newcastle united": "newcastle", "newcastle utd": "newcastle",
    "norwich": "norwich", "norwich city": "norwich",
    "portsmouth": "portsmouth",
    "qpr": "qpr", "queens park rangers": "qpr", "queen park rangers": "qpr",
    "reading": "reading",
    "sheffield utd": "sheffieldutd", "sheffield united": "sheffieldutd",
    "southampton": "southampton",
    "stoke city": "stokecity", "stoke": "stokecity",
    "sunderland": "sunderland",
    "swansea city": "swanseacity", "swansea": "swanseacity",
    "spurs": "spurs", "tottenham": "spurs", "tottenham hotspur": "spurs",
    "watford": "watford", "watford town": "watford",
    "west brom": "westbrom", "west bromwich albion": "westbrom",
    "west ham": "westham", "west ham united": "westham",
    "wigan": "wigan", "wigan athletic": "wigan",
    "wolves": "wolves", "wolverhampton wanderers": "wolves",
}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return "".join(c for c in s.lower() if c.isalpha())


def norm_team(raw: str) -> str:
    return TEAM_ALIASES.get(raw.strip().lower(), _norm(raw))


def build_player_lookup(players_df: pd.DataFrame, teams_df: pd.DataFrame):
    """Build (norm_surname, norm_team) → player_info and norm_surname → [player_info]."""
    tid_name: dict[tuple[str, str], str] = {
        (r.season, r.team_id): r.full_name
        for _, r in teams_df.iterrows()
    }

    friendly_lookup: dict[tuple[str, str], dict] = {}
    surname_all: dict[str, list[dict]] = {}
    seen_perms: set[str] = set()

    for _, row in players_df.iterrows():
        perm = row["fpl_permanent_code"]
        fn = row["friendly_name"]
        parts = fn.split()
        norm_sur = _norm(parts[-1]) if parts else ""
        norm_fn = _norm(fn)
        team_name = tid_name.get((row["season"], row["team_id"]), "")
        nt = norm_team(team_name)

        info = {
            "perm": perm,
            "friendly_name": fn,
            "full_name": row["full_name"],
        }

        for key in [(norm_sur, nt), (norm_fn, nt)]:
            if key[0] and key[1]:
                friendly_lookup[key] = info

        if perm not in seen_perms and norm_sur:
            surname_all.setdefault(norm_sur, []).append(info)
            seen_perms.add(perm)

    return friendly_lookup, surname_all


def extract_name_team(player_code: str) -> tuple[str, str]:
    """From '2017-18-player-S.Mane - Liverpool' extract ('S.Mane', 'Liverpool')."""
    # Strip season prefix and 'player-'
    suffix = player_code.split("-player-", 1)[-1]  # e.g. "S.Mane - Liverpool"
    if " - " in suffix:
        player_str, team_str = suffix.rsplit(" - ", 1)
    else:
        player_str, team_str = suffix, ""
    return player_str.strip(), team_str.strip()


def try_match(
    player_str: str,
    team_str: str,
    friendly_lookup: dict,
    surname_all: dict,
) -> dict | None:
    """Try to match a spreadsheet player string to a players.csv entry."""
    # Normalise surname (after '.' if initial prefix present)
    if "." in player_str:
        sur = player_str.split(".", 1)[1].strip()
    else:
        sur = player_str
    norm_sur = _norm(sur)
    norm_fn = _norm(player_str)
    nt = norm_team(team_str)

    hit = (
        friendly_lookup.get((norm_sur, nt))
        or friendly_lookup.get((norm_fn, nt))
    )
    if not hit:
        # Fall back to surname-only if unambiguous
        hits = surname_all.get(norm_sur, [])
        if len(hits) == 1:
            hit = hits[0]
    return hit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Print to stdout only")
    args = parser.parse_args()

    players_df = pd.read_csv(DATA_DIR / "players.csv", dtype=str).fillna("")
    sel_df = pd.read_csv(DATA_DIR / "manager_selections.csv", dtype=str).fillna("")

    outfield = players_df[players_df["type"] == "player"]
    teams = players_df[players_df["type"] == "team"]
    friendly_lookup, surname_all = build_player_lookup(outfield, teams)

    # Gather unique outfield player codes from historical seasons
    historical = sel_df[
        sel_df["season_id"].isin(HISTORICAL_SEASONS) & (sel_df["position"] != "GK")
    ]
    unique_codes = sorted(historical["player_code"].unique())

    matched: list[tuple[str, dict]] = []
    unmatched: list[str] = []

    for code in unique_codes:
        player_str, team_str = extract_name_team(code)
        hit = try_match(player_str, team_str, friendly_lookup, surname_all)
        if hit:
            matched.append((code, hit))
        else:
            unmatched.append(code)

    print(f"Matched:   {len(matched)}")
    print(f"Unmatched: {len(unmatched)}")

    lines = [
        "# player_aliases.yaml",
        "# Maps synthetic historical player codes -> fpl_permanent_code.",
        "# Edit this file to correct or add entries.",
        "# The dashboard uses this to link historical players to their profiles.",
        "",
        "# --- AUTO-MATCHED (review for errors) ---",
    ]

    # Group by season for readability
    current_season = None
    for code, info in sorted(matched):
        season = code.split("-player-")[0]
        if season != current_season:
            lines.append(f"\n# {season}")
            current_season = season
        fn = info["friendly_name"]
        perm = info["perm"]
        lines.append(f'"{code}": "{perm}"  # {fn}')

    lines += [
        "",
        "# --- UNMATCHED (player retired before 2018-19, add perm code manually if needed) ---",
    ]
    current_season = None
    for code in sorted(unmatched):
        season = code.split("-player-")[0]
        if season != current_season:
            lines.append(f"\n# {season}")
            current_season = season
        lines.append(f'# "{code}": ""')

    output = "\n".join(lines) + "\n"

    if args.dry_run:
        print("\n" + output[:3000] + ("\n... (truncated)" if len(output) > 3000 else ""))
    else:
        out_path = DATA_DIR / "player_aliases.yaml"
        out_path.write_text(output, encoding="utf-8")
        print(f"Written {out_path}")


if __name__ == "__main__":
    main()
