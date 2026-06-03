"""
Fetch badge URLs for historical clubs not in the FPL dataset (pre-2018 relegated teams).
Uses the free thesportsdb.com API (no key needed for search).

Saves results to data/historical_team_badges.yaml.

Usage:
    uv run python scripts/fetch_historical_badges.py [--dry-run]
"""
import argparse
import time
from pathlib import Path

import httpx

DATA_DIR = Path("data")

# Map our friendly_name -> thesportsdb search term
HISTORICAL_CLUBS: dict[str, str] = {
    "Birmingham City": "Birmingham City",
    "Blackburn": "Blackburn Rovers",
    "Bolton": "Bolton Wanderers",
    "Hull City": "Hull City",
    "Middlesbrough": "Middlesbrough",
    "Portsmouth": "Portsmouth",
    "QPR": "Queens Park Rangers",
    "Stoke City": "Stoke City",
    "Swansea City": "Swansea City",
    "Wigan": "Wigan Athletic",
}

TSDB_SEARCH = "https://www.thesportsdb.com/api/v1/json/3/searchteams.php"


def fetch_badge(client: httpx.Client, team_name: str) -> str | None:
    resp = client.get(TSDB_SEARCH, params={"t": team_name}, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    teams = data.get("teams") or []
    for team in teams:
        if team.get("strSport") == "Soccer" and team.get("strCountry") in ("England", "Wales"):
            badge = team.get("strTeamBadge") or team.get("strBadge") or ""
            if badge:
                return badge
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    results: dict[str, str] = {}
    errors: list[str] = []

    with httpx.Client() as client:
        for friendly_name, search_term in HISTORICAL_CLUBS.items():
            print(f"  {friendly_name} ... ", end="", flush=True)
            try:
                url = fetch_badge(client, search_term)
                if url:
                    results[friendly_name] = url
                    print(f"OK")
                else:
                    errors.append(friendly_name)
                    print("NOT FOUND")
            except Exception as e:
                errors.append(friendly_name)
                print(f"ERROR: {e}")
            time.sleep(0.3)  # be polite to the free API

    print(f"\nFetched: {len(results)} / {len(HISTORICAL_CLUBS)}")

    lines = [
        "# historical_team_badges.yaml",
        "# Badge image URLs for clubs not in the FPL dataset (relegated before 2018-19).",
        "# Source: thesportsdb.com (free API). Re-run this script to refresh.",
        "# Maps friendly_name (used in manager_selections.csv) -> badge URL.",
        "",
    ]
    for name, url in sorted(results.items()):
        lines.append(f'"{name}": "{url}"')
    output = "\n".join(lines) + "\n"

    if args.dry_run:
        print("\n" + output)
    else:
        out_path = DATA_DIR / "historical_team_badges.yaml"
        out_path.write_text(output, encoding="utf-8")
        print(f"Written {out_path}")

    if errors:
        print(f"\nNot found: {', '.join(errors)}")
        print("Add them manually to data/historical_team_badges.yaml")


if __name__ == "__main__":
    main()
