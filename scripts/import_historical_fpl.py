"""
Import historical FPL season data from vaastav's Fantasy-Premier-League GitHub repo.

Populates players.csv, goals.csv, and seasons.csv for the requested seasons.
The FPL API only serves the current season — this script fills the gap.

Supported seasons (ID-based player codes, matching 2025-26 convention):
    2023-24, 2024-25

Pre-2023-24 seasons use name-based player codes and will be added once
manager_selections.csv has been imported (so codes can match).

After running this, execute `uv run send-report --preview` or the standings
engine to populate standings.csv for the new seasons.

Usage:
    uv run python scripts/import_historical_fpl.py
    uv run python scripts/import_historical_fpl.py --seasons 2024-25
    uv run python scripts/import_historical_fpl.py --dry-run
"""
import argparse
import sys
import time
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
import pandas as pd

from select_football.common.csv_store import CsvStore
from select_football.config import get_settings

VAASTAV_BASE = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data"

POSITION_MAP = {"1": "GKP", "2": "DEF", "3": "MID", "4": "FWD"}

SEASON_DATES = {
    "2018-19": ("2018-08-11", "2019-05-12"),
    "2019-20": ("2019-08-09", "2020-07-26"),
    "2020-21": ("2020-09-12", "2021-05-23"),
    "2021-22": ("2021-08-14", "2022-05-22"),
    "2022-23": ("2022-08-05", "2023-05-28"),
    "2023-24": ("2023-08-01", "2024-05-20"),
    "2024-25": ("2024-08-11", "2025-05-19"),
}

DEFAULT_SEASONS = [
    "2018-19", "2019-20", "2020-21", "2021-22", "2022-23", "2023-24", "2024-25",
]


def fetch_csv(client: httpx.Client, url: str) -> pd.DataFrame:
    resp = client.get(url)
    resp.raise_for_status()
    return pd.read_csv(StringIO(resp.text), dtype=str)


_MASTER_TEAM_LIST: dict[tuple[str, str], str] | None = None


def _get_master_team_names(client: httpx.Client) -> dict[tuple[str, str], str]:
    global _MASTER_TEAM_LIST
    if _MASTER_TEAM_LIST is None:
        df = fetch_csv(client, f"{VAASTAV_BASE}/master_team_list.csv")
        _MASTER_TEAM_LIST = {
            (str(r["season"]), str(r["team"])): str(r["team_name"])
            for _, r in df.iterrows()
        }
    return _MASTER_TEAM_LIST


def import_season(season_id: str, store: CsvStore, client: httpx.Client, dry_run: bool) -> None:
    print(f"\n=== {season_id} ===")

    # ── Player registry ───────────────────────────────────────────────────
    print("  Fetching player and team registry...")
    players_raw = fetch_csv(client, f"{VAASTAV_BASE}/{season_id}/players_raw.csv")
    master_team_names = _get_master_team_names(client)

    # element_id → team_id and element_id → is_gkp mappings
    elem_to_team: dict[str, str] = {}
    elem_is_gkp: dict[str, bool] = {}
    for _, r in players_raw.iterrows():
        elem_to_team[str(r["id"])] = str(r.get("team", ""))
        elem_is_gkp[str(r["id"])] = str(r.get("element_type", "")) == "1"

    def _photo_url(photo: str) -> str:
        if not photo or str(photo) in ("", "nan"):
            return ""
        return f"https://resources.premierleague.com/premierleague25/photos/players/110x140/{photo.replace('.jpg', '.png')}"

    player_rows = [
        {
            "code": f"{season_id}-player-{r['id']}",
            "season": season_id,
            "type": "player",
            "element_id": r["id"],
            "full_name": f"{r['first_name']} {r['second_name']}",
            "friendly_name": r["web_name"],
            "fpl_position": POSITION_MAP.get(str(r.get("element_type", "")), ""),
            "team_id": r.get("team", ""),
            "team_code": "",
            "status": "A",
            "news": "",
            "news_date": "",
            "photo_url": _photo_url(str(r.get("photo", ""))),
        }
        for _, r in players_raw.iterrows()
    ]

    # Build team rows — use teams.csv if available, otherwise derive from players_raw
    # team_id -> team_code (stable badge identifier, from players_raw.team_code column)
    team_code_from_players: dict[str, str] = {}
    for _, r in players_raw.iterrows():
        tid = str(r.get("team", ""))
        tc = str(r.get("team_code", ""))
        if tid and tc and tc != "nan" and tid not in team_code_from_players:
            team_code_from_players[tid] = tc

    teams_url = f"{VAASTAV_BASE}/{season_id}/teams.csv"
    try:
        teams_raw = fetch_csv(client, teams_url)
        team_rows = [
            {
                "code": f"{season_id}-team-{r['id']}",
                "season": season_id,
                "type": "team",
                "element_id": r["id"],
                "full_name": r["name"],
                "friendly_name": r["name"],
                "fpl_position": "",
                "team_id": r["id"],
                "team_code": str(r.get("code", team_code_from_players.get(str(r["id"]), ""))),
                "status": "",
                "news": "",
                "news_date": "",
                "photo_url": "",
            }
            for _, r in teams_raw.iterrows()
        ]
    except httpx.HTTPStatusError:
        # Older seasons (e.g. 2018-19) have no teams.csv — use master_team_list for names
        seen: set[str] = set()
        team_rows = []
        for _, r in players_raw.iterrows():
            tid = str(r.get("team", ""))
            if tid and tid not in seen:
                seen.add(tid)
                tname = master_team_names.get((season_id, tid), f"Team {tid}")
                team_rows.append({
                    "code": f"{season_id}-team-{tid}",
                    "season": season_id,
                    "type": "team",
                    "element_id": tid,
                    "full_name": tname,
                    "friendly_name": tname,
                    "fpl_position": "",
                    "team_id": tid,
                    "team_code": team_code_from_players.get(tid, ""),
                    "status": "",
                    "news": "",
                    "news_date": "",
                    "photo_url": "",
                })

    print(f"  Players: {len(player_rows)}, Teams: {len(team_rows)}")

    # ── Team goals conceded — from fixture scores ──────────────────────────
    # Use actual match scores from fixtures.csv: counts every goal against
    # (including own goals) and is immune to mid-season loan attribution bugs.
    # Falls back to GKP-stats approach for seasons without fixtures.csv.
    team_conceded: dict[tuple, int] = {}
    _use_fixtures = False
    try:
        fixtures_df = fetch_csv(client, f"{VAASTAV_BASE}/{season_id}/fixtures.csv")
        for _, fix in fixtures_df.iterrows():
            gw_num = str(fix.get("event", ""))
            if not gw_num or gw_num in ("", "nan"):
                continue
            team_h = str(fix.get("team_h", ""))
            team_a = str(fix.get("team_a", ""))
            sh_raw = fix.get("team_h_score", "")
            sa_raw = fix.get("team_a_score", "")
            if str(sh_raw) in ("", "nan") or str(sa_raw) in ("", "nan"):
                continue  # fixture not yet played
            score_h, score_a = int(float(sh_raw)), int(float(sa_raw))
            if team_h and score_a > 0:
                team_conceded[(team_h, gw_num)] = team_conceded.get((team_h, gw_num), 0) + score_a
            if team_a and score_h > 0:
                team_conceded[(team_a, gw_num)] = team_conceded.get((team_a, gw_num), 0) + score_h
        _use_fixtures = True
        print(f"  Team conceded: {len(team_conceded)} entries from fixtures.csv")
    except httpx.HTTPStatusError:
        print("  fixtures.csv not found — will derive conceded from GKP stats")

    # ── GW player goals ────────────────────────────────────────────────────
    # player_goals: (element_id, round) → total goals_scored across all fixtures in that GW
    player_goals: dict[tuple, int] = {}
    last_gw = 0
    # Fallback for seasons without fixtures.csv: track (team_id, fixture_id) to avoid
    # double-counting when multiple GKPs play in the same fixture (e.g. emergency sub).
    seen_team_fixtures: set[tuple] = set()

    for gw in range(1, 50):
        url = f"{VAASTAV_BASE}/{season_id}/gws/gw{gw}.csv"
        try:
            gw_df = fetch_csv(client, url)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                print(f"  GW{gw} not found — {last_gw} GWs loaded")
                break
            raise
        last_gw = gw

        for _, r in gw_df.iterrows():
            elem = r.get("element", "")
            if not elem:
                continue
            gw_num = r.get("round", str(gw))
            scored = int(float(r.get("goals_scored", 0) or 0))
            minutes = int(float(r.get("minutes", 0) or 0))

            if scored > 0:
                key = (elem, gw_num)
                player_goals[key] = player_goals.get(key, 0) + scored

            # GKP fallback only when fixtures.csv was not available
            if not _use_fixtures and elem_is_gkp.get(elem, False) and minutes > 0:
                conceded = int(float(r.get("goals_conceded", 0) or 0))
                if conceded > 0:
                    fixture_id = str(r.get("fixture", ""))
                    team_id = elem_to_team.get(elem, "")
                    if team_id:
                        f_key = (team_id, fixture_id) if fixture_id else None
                        if f_key and f_key not in seen_team_fixtures:
                            seen_team_fixtures.add(f_key)
                            t_key = (team_id, gw_num)
                            team_conceded[t_key] = team_conceded.get(t_key, 0) + conceded
                        elif not f_key:
                            t_key = (team_id, gw_num)
                            team_conceded[t_key] = team_conceded.get(t_key, 0) + conceded

        time.sleep(0.05)

    goal_rows = [
        {
            "player_code": f"{season_id}-player-{elem}",
            "season_id": season_id,
            "game_week": gw_num,
            "goals_scored": str(scored),
            "goals_conceded": "0",
        }
        for (elem, gw_num), scored in player_goals.items()
    ] + [
        {
            "player_code": f"{season_id}-team-{team_id}",
            "season_id": season_id,
            "game_week": gw_num,
            "goals_scored": "0",
            "goals_conceded": str(conceded),
        }
        for (team_id, gw_num), conceded in team_conceded.items()
    ]

    print(f"  Goal rows: {len(goal_rows)} ({len(player_goals)} player, {len(team_conceded)} team)")

    if dry_run:
        print("  Dry run — no writes.")
        return

    # ── Write ─────────────────────────────────────────────────────────────
    store.upsert("players", pd.DataFrame(player_rows + team_rows), key_cols=["code"])

    # Replace all goal rows for this season — upsert alone won't clear stale rows
    # for GWs where a team had 0 goals conceded under the new approach (e.g., clean
    # sheets that the old GKP approach had wrongly attributed to another team).
    existing_goals = store.read("goals")
    if not existing_goals.empty and "season_id" in existing_goals.columns:
        existing_goals = existing_goals[existing_goals["season_id"] != season_id]
        store.write("goals", pd.concat([existing_goals, pd.DataFrame(goal_rows)], ignore_index=True))
    else:
        store.upsert("goals", pd.DataFrame(goal_rows), key_cols=["player_code", "season_id", "game_week"])

    # Ensure season row exists with last_gw_synced set
    seasons_df = store.read("seasons")
    start, end = SEASON_DATES.get(season_id, ("", ""))
    if season_id not in seasons_df.get("season_id", pd.Series([], dtype=str)).values:
        store.upsert(
            "seasons",
            pd.DataFrame([{
                "season_id": season_id,
                "start_date": start,
                "end_date": end,
                "last_gw_synced": str(last_gw),
            }]),
            key_cols=["season_id"],
        )
        print(f"  Added {season_id} to seasons.csv")
    else:
        store.upsert(
            "seasons",
            pd.DataFrame([{"season_id": season_id, "last_gw_synced": str(last_gw)}]),
            key_cols=["season_id"],
        )

    print(f"  Written. last_gw_synced = {last_gw}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--seasons", nargs="+", default=DEFAULT_SEASONS,
        metavar="SEASON",
        help=f"Season IDs to import (default: {' '.join(DEFAULT_SEASONS)})",
    )
    parser.add_argument("--dry-run", action="store_true", help="Fetch data but do not write anything")
    args = parser.parse_args()

    settings = get_settings()
    store = CsvStore(settings.data_dir)

    with httpx.Client(
        timeout=30,
        headers={"User-Agent": "select-football-auction/1.0"},
        follow_redirects=True,
    ) as client:
        for season_id in args.seasons:
            import_season(season_id, store, client, dry_run=args.dry_run)

    print("\nDone. Run `uv run send-report --preview` to compute standings for new seasons.")


if __name__ == "__main__":
    main()
