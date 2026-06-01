"""
Import 2024-25 manager selections by matching player full_name + team_name
against players.csv rather than relying on player codes (which shifted between
vaastav historical IDs and current FPL IDs).
"""
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd

from select_football.common.csv_store import CsvStore
from select_football.config import get_settings

SEASON = "2024-25"
RAW_TSV = Path(__file__).parent / "selections_2024_25_raw.tsv"


def _normalise(s: str) -> str:
    """Lower-case, strip accents, collapse whitespace."""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return " ".join(s.lower().split())


def main() -> None:
    settings = get_settings()
    store = CsvStore(settings.data_dir)
    players_df = store.read("players")

    # ── Build lookups from players.csv for 2024-25 ───────────────────────────
    p2425 = players_df[players_df["season"] == SEASON]
    teams_2425 = p2425[p2425["type"] == "team"]
    players_2425 = p2425[p2425["type"] == "player"]

    # team friendly_name (normalised) -> team code
    team_name_to_code: dict[str, str] = {
        _normalise(r["friendly_name"]): r["code"]
        for _, r in teams_2425.iterrows()
    }

    # Build player lookups: normalised full_name -> list of (code, team_id)
    # and normalised friendly_name -> list of (code, team_id)
    full_name_map: dict[str, list[tuple[str, str]]] = {}
    friendly_map: dict[str, list[tuple[str, str]]] = {}
    for _, r in players_2425.iterrows():
        fn = _normalise(r.get("full_name", "") or "")
        wb = _normalise(r.get("friendly_name", "") or "")
        tid = str(r.get("team_id", ""))
        if fn:
            full_name_map.setdefault(fn, []).append((r["code"], tid))
        if wb:
            friendly_map.setdefault(wb, []).append((r["code"], tid))

    # team_id -> team friendly_name (for resolving team column in TSV)
    tid_to_name: dict[str, str] = {
        str(r["element_id"]): _normalise(r["friendly_name"])
        for _, r in teams_2425.iterrows()
    }

    # ── Parse raw TSV ─────────────────────────────────────────────────────────
    rows = []
    unmatched = []
    with open(RAW_TSV, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 8:
                continue
            _orig_code, season_id, manager, name, position, cost, gw_from, gw_to = parts[:8]
            team_col = parts[8].strip() if len(parts) > 8 else ""
            position = position.upper()

            if position == "GK":
                # Match team by name
                key = _normalise(name)
                code = team_name_to_code.get(key)
                if not code:
                    unmatched.append(f"GK team '{name}' not found")
                    continue
            else:
                # Match player by full_name, verified by team
                team_key = _normalise(team_col) if team_col else None
                code = None

                # Try full_name first, then friendly_name
                for lookup in (full_name_map, friendly_map):
                    candidates = lookup.get(_normalise(name), [])
                    if not candidates:
                        continue
                    if len(candidates) == 1:
                        code = candidates[0][0]
                        break
                    # Multiple candidates — filter by team
                    if team_key:
                        matched = [
                            c for c, tid in candidates
                            if tid_to_name.get(tid) == team_key
                        ]
                        if matched:
                            code = matched[0]
                            break
                    # Still ambiguous — take first
                    code = candidates[0][0]
                    break

                if not code:
                    unmatched.append(f"'{name}' ({team_col}) not found")
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

    # Show summary
    print("\nSelections per manager:")
    print(sel_df.groupby("manager_name").size().to_string())


if __name__ == "__main__":
    main()
