"""
Sync player metadata and goals from vaastav for historical seasons (2016-17 to 2024-25).

Sources:
  - players_raw.csv  → vaastav_players.csv (historical player registry)
  - gws/gw{n}.csv    → goals.csv (goals_scored per player per GW)
  - fixtures.csv     → goals.csv (goals_conceded per GK team per GW)

After syncing players, builds player_links.csv which maps every season-specific
player code to a permanent FPL player code (stable across seasons).

Usage:
    uv run python scripts/sync_vaastav.py [--season 2024-25] [--dry-run]
    uv run python scripts/sync_vaastav.py --players-only
    uv run python scripts/sync_vaastav.py --goals-only
    uv run python scripts/sync_vaastav.py --links-only   # just rebuild player_links.csv
"""

import argparse
import io
import unicodedata
import urllib.request
from pathlib import Path

import pandas as pd
import yaml  # type: ignore[import-untyped]

DATA_DIR = Path("data")
VAASTAV_BASE = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data"

VAASTAV_SEASONS = [
    "2018-19", "2019-20", "2020-21",
    "2021-22", "2022-23", "2023-24", "2024-25",
]

GW_COUNTS = {"2019-20": 47}
DEFAULT_GW_COUNT = 38


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return "".join(c for c in s.lower() if c.isalpha())


def _safe_print(s: str) -> None:
    print(s.encode("ascii", "replace").decode("ascii"))


def fetch_csv(url: str) -> pd.DataFrame | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "python-urllib"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        try:
            return pd.read_csv(io.BytesIO(data), dtype=str).fillna("")
        except UnicodeDecodeError:
            return pd.read_csv(io.BytesIO(data), dtype=str, encoding="latin-1").fillna("")
    except Exception as e:
        print(f"  WARN fetch {url}: {e}")
        return None


def is_closed(season_id: str) -> bool:
    seasons_path = DATA_DIR / "seasons.csv"
    if not seasons_path.exists():
        return False
    seasons = pd.read_csv(seasons_path, dtype=str)
    row = seasons[seasons["season_id"] == season_id]
    if row.empty:
        return False
    return str(row["closed"].iloc[0]).strip().lower() == "true"


def sync_players_from_vaastav(season_id: str, dry_run: bool) -> int:
    """Sync outfield player metadata from vaastav into vaastav_players.csv."""
    url = f"{VAASTAV_BASE}/{season_id}/players_raw.csv"
    raw = fetch_csv(url)
    if raw is None:
        print(f"  ERROR: could not fetch {url}")
        return 0

    teams_url = f"{VAASTAV_BASE}/{season_id}/teams.csv"
    teams_raw = fetch_csv(teams_url)
    team_map: dict[str, str] = {}
    if teams_raw is not None:
        for _, row in teams_raw.iterrows():
            team_map[str(row.get("id", "")).strip()] = str(row.get("name", "")).strip()

    id_col = next((c for c in ("id", "element") if c in raw.columns), None)
    if id_col is None:
        print("  ERROR: no id/element column in players_raw.csv")
        return 0

    # Build team_code → name lookup from known seasons already in players.csv
    code_to_name: dict[str, str] = {}
    players_path = DATA_DIR / "players.csv"
    if players_path.exists() and players_path.stat().st_size > 0:
        existing_teams = pd.read_csv(players_path, dtype=str).fillna("")
        existing_teams = existing_teams[existing_teams["type"] == "team"]
        for _, tr in existing_teams.iterrows():
            tc = tr.get("team_code", "").strip()
            tn = tr.get("full_name", "").strip()
            if tc and tn and tc not in code_to_name:
                code_to_name[tc] = tn

    team_rows = []
    if teams_raw is not None:
        for _, trow in teams_raw.iterrows():
            try:
                tid = int(str(trow.get("id", "")).strip())
            except (ValueError, TypeError):
                continue
            t_code = str(trow.get("code", "")).strip()
            t_name = str(trow.get("name", "")).strip()
            team_rows.append({
                "code": f"{season_id}-team-{tid}",
                "season": season_id,
                "type": "team",
                "element_id": str(tid),
                "fpl_permanent_code": "",
                "full_name": t_name,
                "friendly_name": t_name,
                "fpl_position": "",
                "team_id": str(tid),
                "team_code": t_code,
                "status": "",
                "news": "",
                "news_date": "",
                "photo_url": f"https://resources.premierleague.com/premierleague/badges/t{t_code}.png" if t_code else "",
            })
    else:
        # No teams.csv — derive unique teams from players_raw team/team_code columns
        seen_tids: set[int] = set()
        for _, row in raw.iterrows():
            try:
                tid = int(str(row.get("team", "")).strip())
            except (ValueError, TypeError):
                continue
            if tid in seen_tids:
                continue
            seen_tids.add(tid)
            t_code = str(row.get("team_code", "")).strip()
            t_name = code_to_name.get(t_code, "")
            team_rows.append({
                "code": f"{season_id}-team-{tid}",
                "season": season_id,
                "type": "team",
                "element_id": str(tid),
                "fpl_permanent_code": "",
                "full_name": t_name,
                "friendly_name": t_name,
                "fpl_position": "",
                "team_id": str(tid),
                "team_code": t_code,
                "status": "",
                "news": "",
                "news_date": "",
                "photo_url": f"https://resources.premierleague.com/premierleague/badges/t{t_code}.png" if t_code else "",
            })

    rows = []
    for _, row in raw.iterrows():
        try:
            eid = int(str(row[id_col]).strip())
        except (ValueError, TypeError):
            continue

        fpl_perm_code = str(row.get("code", "")).strip()
        web = str(row.get("web_name", "")).strip()
        first = str(row.get("first_name", "")).strip()
        second = str(row.get("second_name", "")).strip()
        el_type = str(row.get("element_type", "")).strip()
        team_id = str(row.get("team", "")).strip()
        team_code = str(row.get("team_code", "")).strip()

        pos_map = {"1": "GKP", "2": "DEF", "3": "MID", "4": "FWD"}
        fpl_pos = pos_map.get(el_type, el_type)

        photo_url = (
            f"https://resources.premierleague.com/premierleague/photos/players/110x140/p{fpl_perm_code}.png"
            if fpl_perm_code and fpl_perm_code != "0" else ""
        )
        rows.append({
            "code": f"{season_id}-player-{eid}",
            "season": season_id,
            "type": "player",
            "element_id": str(eid),
            "fpl_permanent_code": fpl_perm_code,
            "full_name": f"{first} {second}".strip(),
            "friendly_name": web,
            "fpl_position": fpl_pos,
            "team_id": team_id,
            "team_code": team_code,
            "status": str(row.get("status", "")).strip(),
            "news": str(row.get("news", "")).strip(),
            "news_date": "",
            "photo_url": photo_url,
        })

    _safe_print(f"  {len(team_rows)} teams, {len(rows)} players found in vaastav {season_id}")

    all_rows = team_rows + rows
    if not dry_run:
        new_df = pd.DataFrame(all_rows)
        players_path = DATA_DIR / "players.csv"
        if players_path.exists() and players_path.stat().st_size > 0:
            existing = pd.read_csv(players_path, dtype=str).fillna("")
            existing = existing[existing["season"] != season_id]
            result = pd.concat([existing, new_df], ignore_index=True)
        else:
            result = new_df
        result.to_csv(players_path, index=False)

    return len(all_rows)


def sync_goals_from_vaastav(season_id: str, dry_run: bool) -> dict:
    """Sync goals from vaastav GW files and fixtures into goals.csv.

    Syncs ALL players and ALL teams for the season — no dependency on
    manager_selections.csv. Goals are filtered by scoring engine at query time.
    """
    # Build element_id → player_code map from players.csv
    players_path = DATA_DIR / "players.csv"
    elem_codes: dict[int, str] = {}
    all_team_ids: list[int] = []

    if players_path.exists() and players_path.stat().st_size > 0:
        pdf = pd.read_csv(players_path, dtype=str).fillna("")
        season_pdf = pdf[pdf["season"] == season_id]
        for _, row in season_pdf[season_pdf["type"] == "player"].iterrows():
            try:
                eid = int(str(row["element_id"]).strip())
                elem_codes[eid] = str(row["code"])
            except (ValueError, TypeError):
                continue
        for _, row in season_pdf[season_pdf["type"] == "team"].iterrows():
            try:
                all_team_ids.append(int(str(row["element_id"]).strip()))
            except (ValueError, TypeError):
                continue

    if not elem_codes:
        print(f"  WARN: no players found in players.csv for {season_id} — run --players-only first")
        return {}

    _safe_print(f"  Syncing goals for {len(elem_codes)} players, {len(all_team_ids)} teams")

    n_outfield_gws = 0
    n_conceded_gws = 0
    gw_count = GW_COUNTS.get(season_id, DEFAULT_GW_COUNT)
    goals_by_player: dict[int, list[dict]] = {}

    print(f"  Fetching {gw_count} GW files for {season_id}...")
    for gw in range(1, gw_count + 1):
        url = f"{VAASTAV_BASE}/{season_id}/gws/gw{gw}.csv"
        gw_df = fetch_csv(url)
        if gw_df is None:
            continue

        id_col = next((c for c in ("element", "id") if c in gw_df.columns), None)
        if id_col is None:
            continue

        for _, row in gw_df.iterrows():
            try:
                eid = int(str(row[id_col]).strip())
            except (ValueError, TypeError):
                continue

            if eid not in elem_codes:
                continue

            try:
                scored = int(str(row.get("goals_scored", "0")).strip() or "0")
            except (ValueError, TypeError):
                scored = 0

            if scored > 0:
                goals_by_player.setdefault(eid, []).append({
                    "player_code": elem_codes[eid],
                    "season_id": season_id,
                    "game_week": str(gw),
                    "goals_scored": str(scored),
                    "goals_conceded": "0",
                })
                n_outfield_gws += 1

    fixtures_url = f"{VAASTAV_BASE}/{season_id}/fixtures.csv"
    fixtures = fetch_csv(fixtures_url)
    conceded_by_team: dict[int, dict[int, int]] = {}

    if fixtures is not None:
        for _, row in fixtures.iterrows():
            try:
                finished = str(row.get("finished", "False")).strip().lower()
                if finished not in ("true", "1"):
                    continue
                gw = int(str(row.get("event", "0")).strip() or "0")
                if gw == 0:
                    continue
                team_h = int(str(row.get("team_h", "0")).strip())
                team_a = int(str(row.get("team_a", "0")).strip())
                score_h = int(str(row.get("team_h_score", "0")).strip() or "0")
                score_a = int(str(row.get("team_a_score", "0")).strip() or "0")
            except (ValueError, TypeError):
                continue

            for team_id, conceded in [(team_h, score_a), (team_a, score_h)]:
                if team_id in all_team_ids and conceded > 0:
                    conceded_by_team.setdefault(team_id, {})
                    conceded_by_team[team_id][gw] = conceded_by_team[team_id].get(gw, 0) + conceded

    gk_rows = []
    for team_id, gw_data in conceded_by_team.items():
        for gw, conceded in gw_data.items():
            gk_rows.append({
                "player_code": f"{season_id}-team-{team_id}",
                "season_id": season_id,
                "game_week": str(gw),
                "goals_scored": "0",
                "goals_conceded": str(conceded),
            })
            n_conceded_gws += 1

    all_goal_rows = [r for rows in goals_by_player.values() for r in rows] + gk_rows
    _safe_print(f"  Goals: {n_outfield_gws} outfield GWs, {n_conceded_gws} GK conceded GWs")

    if not dry_run and all_goal_rows:
        new_df = pd.DataFrame(all_goal_rows)
        goals_path = DATA_DIR / "goals.csv"
        if goals_path.exists() and goals_path.stat().st_size > 0:
            existing = pd.read_csv(goals_path, dtype=str).fillna("")
            existing = existing[existing["season_id"] != season_id]
            result = pd.concat([existing, new_df], ignore_index=True)
        else:
            result = new_df
        result.to_csv(goals_path, index=False)

    return {"outfield": n_outfield_gws, "gk_conceded": n_conceded_gws}


def build_player_links(dry_run: bool = False) -> int:
    """Build player_links.csv from vaastav_players.csv + players.csv + overrides.

    Maps every season-specific player code to a permanent FPL player code.
    The permanent code is stable across seasons and allows cross-season lookups.
    """
    rows: list[dict] = []

    # From master players.csv (outfield + teams, all seasons)
    players_path = DATA_DIR / "players.csv"
    if players_path.exists() and players_path.stat().st_size > 0:
        pdf = pd.read_csv(players_path, dtype=str).fillna("")
        if "fpl_permanent_code" in pdf.columns:
            for _, r in pdf.iterrows():
                perm = r["fpl_permanent_code"].strip()
                if perm and perm != "0":
                    rows.append({
                        "season_code": r["code"],
                        "fpl_permanent_code": perm,
                        "full_name": r.get("full_name", ""),
                        "season_id": r.get("season", ""),
                    })

    # Manual overrides from YAML
    overrides_path = DATA_DIR / "player_links_overrides.yaml"
    if overrides_path.exists():
        with open(overrides_path, encoding="utf-8") as f:
            overrides = yaml.safe_load(f) or {}
        for season_code, entry in (overrides.get("links") or {}).items():
            if isinstance(entry, dict):
                perm = str(entry.get("fpl_permanent_code", ""))
                name = str(entry.get("full_name", ""))
            else:
                perm = str(entry)
                name = ""
            if perm:
                season_id = "-".join(season_code.split("-")[:2]) if "-player-" in season_code else ""
                rows.append({
                    "season_code": season_code,
                    "fpl_permanent_code": perm,
                    "full_name": name,
                    "season_id": season_id,
                })

    if not rows:
        print("  No player data found to build links from.")
        return 0

    df = pd.DataFrame(rows).drop_duplicates(subset=["season_code"], keep="last")
    df = df.sort_values(["fpl_permanent_code", "season_id"]).reset_index(drop=True)

    _safe_print(f"  player_links: {len(df)} entries ({df['fpl_permanent_code'].nunique()} unique players)")

    if not dry_run:
        df.to_csv(DATA_DIR / "player_links.csv", index=False)

    return len(df)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync player data and goals from vaastav")
    parser.add_argument("--season", help="Season ID e.g. 2024-25 (default: all vaastav seasons)")
    parser.add_argument("--dry-run", action="store_true", help="Do not write any changes")
    parser.add_argument("--players-only", action="store_true", help="Sync players but not goals")
    parser.add_argument("--goals-only", action="store_true", help="Sync goals but not players")
    parser.add_argument("--links-only", action="store_true", help="Just rebuild player_links.csv")
    args = parser.parse_args()

    if args.links_only:
        print("\n=== Building player_links.csv ===")
        n = build_player_links(dry_run=args.dry_run)
        print(f"  Links written: {n}")
        return

    seasons = [args.season] if args.season else VAASTAV_SEASONS

    for season_id in seasons:
        if season_id not in VAASTAV_SEASONS:
            print(f"SKIP {season_id}: not a vaastav-supported season")
            continue

        if is_closed(season_id):
            print(f"SKIP {season_id}: season is closed")
            continue

        print(f"\n=== {season_id} ===")

        if not args.goals_only:
            n = sync_players_from_vaastav(season_id, dry_run=args.dry_run)
            print(f"  Players synced: {n}")

        if not args.players_only:
            stats = sync_goals_from_vaastav(season_id, dry_run=args.dry_run)
            print(f"  Goals synced: {stats}")

    # Always rebuild player links after syncing players
    if not args.goals_only and not args.dry_run:
        print("\n=== Building player_links.csv ===")
        n = build_player_links(dry_run=False)
        print(f"  Links written: {n}")

    if args.dry_run:
        print("\nDry run -- no changes written.")
    else:
        print("\nDone.")


if __name__ == "__main__":
    main()
