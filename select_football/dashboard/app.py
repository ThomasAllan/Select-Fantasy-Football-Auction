import re as _re_aliases
import urllib.parse
from datetime import datetime
from pathlib import Path

import httpx
import pandas as pd
import streamlit as st

from select_football.common.csv_store import CsvStore
from select_football.config import get_settings
from select_football.core.scoring import score_gk_team, score_outfield_player
from select_football.core.standings import (
    _goals_for,
    _gk_player_codes_for_team,
    _int,
)

st.set_page_config(
    page_title="Select Fantasy Football Auction",
    page_icon="⚽",
    layout="wide",
)

st.markdown(
    "<style>"
    "[data-testid='stDataFrame'] [class*='resizer'], .ag-header-cell-resize { display: none !important; }"
    "[data-testid='stMetricValue'] { font-size: 1.35rem !important; }"
    "[data-testid='stMetricLabel'] { font-size: 0.8rem !important; }"
    "</style>",
    unsafe_allow_html=True,
)


# ── Alias helpers ──────────────────────────────────────────────────────────────

def _load_historical_badges(data_dir: Path) -> dict[str, str]:
    """Parse data/historical_team_badges.yaml → {friendly_name: badge_url}."""
    path = data_dir / "historical_team_badges.yaml"
    if not path.exists():
        return {}
    pattern = _re_aliases.compile(r'"([^"]+)":\s*"([^"]+)"')
    badges: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("#") or not line:
            continue
        m = pattern.search(line)
        if m:
            badges[m.group(1)] = m.group(2)
    return badges


def _load_player_aliases(data_dir: Path) -> dict[str, str]:
    """Parse data/player_aliases.yaml → {synthetic_player_code: fpl_permanent_code}."""
    path = data_dir / "player_aliases.yaml"
    if not path.exists():
        return {}
    pattern = _re_aliases.compile(r'"([^"]+)":\s*"([^"]+)"')
    aliases: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("#"):
            continue
        m = pattern.search(line)
        if m:
            aliases[m.group(1)] = m.group(2)
    return aliases


def _augment_players_with_aliases(
    players_raw: pd.DataFrame, aliases: dict[str, str]
) -> pd.DataFrame:
    """
    For each alias entry that maps a synthetic historical code to an fpl_permanent_code,
    add a synthetic players_df row so the Players tab can surface historical season data.
    The synthetic row copies display fields (name, photo, position) from the player's
    most recent real entry but uses the historical season and synthetic code.
    """
    if not aliases:
        return players_raw

    outfield = players_raw[players_raw["type"] == "player"].sort_values(
        "season", ascending=False
    )
    perm_to_info: dict[str, dict] = {}
    for _, row in outfield.iterrows():
        perm = str(row.get("fpl_permanent_code", "") or "").strip()
        if perm and perm not in perm_to_info:
            perm_to_info[perm] = row.to_dict()

    existing_codes = set(players_raw["code"])
    synthetic_rows: list[dict] = []
    for code, perm in aliases.items():
        if code in existing_codes or perm not in perm_to_info:
            continue
        parts = code.split("-player-", 1)
        if len(parts) != 2:
            continue
        season = parts[0]
        row_dict = perm_to_info[perm].copy()
        row_dict.update({
            "code": code,
            "season": season,
            "team_id": "",
            "team_code": "",
            "status": "",
            "news": "",
            "news_date": "",
        })
        synthetic_rows.append(row_dict)

    if not synthetic_rows:
        return players_raw
    return pd.concat(
        [players_raw, pd.DataFrame(synthetic_rows)], ignore_index=True
    )


# ── Data loading ───────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def load_data() -> dict:
    settings = get_settings()
    store = CsvStore(settings.data_dir)
    data_dir = Path(settings.data_dir)
    goals_path = data_dir / "goals.csv"
    last_updated = (
        datetime.fromtimestamp(goals_path.stat().st_mtime).strftime("%d %b %Y, %H:%M")
        if goals_path.exists()
        else None
    )
    players_raw = store.read_all_players()
    aliases = _load_player_aliases(data_dir)
    players_augmented = _augment_players_with_aliases(players_raw, aliases)
    return {
        "seasons": store.read("seasons"),
        "selections": store.read("manager_selections"),
        "goals": store.read("goals"),
        "overrides": store.read("overrides"),
        "players": players_augmented,
        "prizes": store.read("prizes"),
        "standings": store.read("standings"),
        "best_gameweeks": store.read("best_gameweeks"),
        "last_updated": last_updated,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

@st.cache_data(ttl=3600)
def _photo_exists(url: str) -> bool:
    try:
        r = httpx.get(url, timeout=3, follow_redirects=True)
        return r.status_code == 200
    except Exception:
        return False



_TEAM_SUFFIX_NORM: dict[str, str] = {
    "a.villa": "Aston Villa",
    "birmingham": "Birmingham City",
    "brighton ha": "Brighton",
    "c.palace": "Crystal Palace",
    "huddersfield town": "Huddersfield",
    "leicester city": "Leicester",
    "man utd.": "Man Utd",
    "newcastle utd": "Newcastle",
    "norwich city": "Norwich",
    "stoke": "Stoke City",
    "swansea": "Swansea City",
    "wba": "West Brom",
    "west ham utd": "West Ham",
}

_INITIAL_RE = _re_aliases.compile(r"^[A-Z]\.")


def _strip_initial(name: str) -> str:
    """'S.Downing' → 'Downing', 'Rafael Da Silva' → 'Rafael Da Silva'."""
    return _INITIAL_RE.sub("", name).strip()


def _norm_team_suffix(raw: str) -> str:
    """Normalise a raw team string extracted from a historical player code."""
    return _TEAM_SUFFIX_NORM.get(raw.lower(), raw)


def _code_display_name(code: str, player_names: dict) -> str:
    """Return a human-readable name for a player/team code, falling back to parsing the code."""
    name = player_names.get(code)
    if name:
        return name
    if "-player-" in code:
        suffix = code.split("-player-", 1)[-1]
        raw = suffix.split(" - ")[0].strip() if " - " in suffix else suffix
        return _strip_initial(raw)
    if "-team-" in code:
        return code.split("-team-", 1)[-1]
    return code


def _code_team_name(code: str, pos: str, team_id: str, team_names_map: dict, player_names_map: dict) -> str:
    """Return the club name for a squad row, falling back to parsing historical codes."""
    if pos == "GK":
        name = player_names_map.get(code)
        if name:
            return name
        if "-team-" in code:
            return code.split("-team-", 1)[-1]
        return "—"
    if team_id:
        name = team_names_map.get(team_id, "")
        if name:
            return name
    if "-player-" in code:
        suffix = code.split("-player-", 1)[-1]
        if " - " in suffix:
            return _norm_team_suffix(suffix.rsplit(" - ", 1)[-1].strip())
    return "—"


def _ordinal(n: int) -> str:
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(n if n < 20 else n % 10, "th")
    return f"{n}{suffix}"



def _pos_colour(pos: int, prize_positions: set) -> str:
    colours = {1: "#2563eb", 2: "#15803d", 3: "#86efac"}
    if pos in colours:
        return colours[pos]
    if pos in prize_positions:
        return "#f59e0b"
    return "transparent"


def render_standings_table(standings_df: pd.DataFrame, prizes_df: pd.DataFrame, season_id: str, season_finished: bool = False) -> None:
    if standings_df.empty or "season_id" not in standings_df.columns:
        st.info("No standings data available. Run sync-scores first.")
        return
    rows = standings_df[standings_df["season_id"] == season_id]
    if rows.empty:
        st.info("No standings data for this season.")
        return

    prize_map = {
        int(r["position"]): float(r["prize_amount"])
        for _, r in prizes_df[prizes_df["season_id"] == season_id].iterrows()
    }
    rows = rows.assign(position=rows["position"].astype(int)).sort_values("position")

    # Build shared prize map: managers tied on points pool their prizes and split equally
    from collections import defaultdict
    pts_to_positions: dict[int, list[int]] = defaultdict(list)
    for _, r in rows.iterrows():
        pts_to_positions[int(float(r["total_points"]))].append(int(r["position"]))

    shared_prize_map: dict[int, float | None] = {}
    prize_positions: set[int] = set()
    for positions in pts_to_positions.values():
        total_prize = sum(prize_map.get(p, 0.0) for p in positions)
        share = (total_prize / len(positions)) if total_prize else None
        for p in positions:
            shared_prize_map[p] = share
            if share:
                prize_positions.add(p)

    # Build display label for every position — tied positions show "=N" (lowest in group)
    pos_display_map: dict[int, str] = {}
    for _grp_pts, _grp_positions in pts_to_positions.items():
        _label = f"={min(_grp_positions)}" if len(_grp_positions) > 1 else str(min(_grp_positions))
        for _p in _grp_positions:
            pos_display_map[_p] = _label

    # Positions tied at the top (share the highest points total)
    _top_pts = int(float(rows.iloc[0]["total_points"])) if not rows.empty else 0
    _top_positions: set[int] = set(pts_to_positions.get(_top_pts, []))

    tr_parts = []
    for i, (_, r) in enumerate(rows.iterrows()):
        pos = int(r["position"])
        name = r["manager_name"]
        pts = int(float(r["total_points"]))
        prize_val = shared_prize_map.get(pos)
        is_shared = prize_val is not None and len(pts_to_positions[pts]) > 1 and prize_val > 0
        prize_str = (f"£{prize_val:.0f} ea" if is_shared else f"£{prize_val:.0f}") if prize_val else "—"
        is_champion = season_finished and pos in _top_positions
        if is_champion:
            prize_str += " 🏆"
        pos_display = pos_display_map.get(pos, str(pos))
        colour = _pos_colour(pos, prize_positions)
        border = f"border-left:4px solid {colour};" if colour != "transparent" else "border-left:4px solid transparent;"
        stripe = "background:rgba(128,128,128,0.06);" if i % 2 == 1 else ""
        encoded = urllib.parse.quote(name)
        encoded_szn = urllib.parse.quote(season_id)
        tr_parts.append(
            f'<tr style="{stripe}">'
            f'<td style="{border}padding:8px 14px;text-align:center;font-weight:600">{pos_display}</td>'
            f'<td style="padding:8px 14px"><a href="?manager={encoded}&season={encoded_szn}&table_season={encoded_szn}" target="_self" style="color:#60a5fa;text-decoration:none;font-weight:500">{name}</a></td>'
            f'<td style="padding:8px 14px;text-align:center">{pts}</td>'
            f'<td style="padding:8px 14px;text-align:center">{prize_str}</td>'
            f'</tr>'
        )

    html = (
        '<style>'
        'table.lt{width:100%;border-collapse:collapse;font-size:0.92em}'
        'table.lt th{padding:7px 14px;text-align:left;border-bottom:2px solid #e5e7eb;color:#6b7280;font-weight:600;font-size:0.75em;text-transform:uppercase;letter-spacing:0.04em}'
        'table.lt th:nth-child(1),table.lt th:nth-child(3),table.lt th:nth-child(4){text-align:center}'
        'table.lt tr:hover td{background:rgba(128,128,128,0.1)!important}'
        'table.lt a:hover{text-decoration:underline!important}'
        '</style>'
        '<table class="lt"><thead><tr>'
        '<th>Pos</th><th>Manager</th><th>Points</th><th>Prize</th>'
        f'</tr></thead><tbody>{"".join(tr_parts)}</tbody></table>'
    )
    st.markdown(html, unsafe_allow_html=True)


def get_all_managers(data: dict) -> list[str]:
    names: set[str] = set()
    for df, col in [
        (data["standings"], "manager_name"),
        (data["selections"], "manager_name"),
    ]:
        if not df.empty and col in df.columns:
            names.update(df[col].dropna().tolist())
    return sorted(names)


def get_manager_history(standings_df: pd.DataFrame, prizes_df: pd.DataFrame, manager_name: str) -> pd.DataFrame:
    if standings_df.empty or "manager_name" not in standings_df.columns:
        return pd.DataFrame()
    rows = standings_df[standings_df["manager_name"] == manager_name].copy()
    if rows.empty:
        return pd.DataFrame()
    out = []
    for _, r in rows.iterrows():
        sid = r["season_id"]
        prize_map = {
            int(p["position"]): float(p["prize_amount"])
            for _, p in prizes_df[prizes_df["season_id"] == sid].iterrows()
        }
        pos = int(r["position"])
        pts = int(float(r["total_points"]))
        # Resolve shared prizes for tied managers in this season
        season_rows = standings_df[standings_df["season_id"] == sid]
        tied = season_rows[season_rows["total_points"].astype(float).astype(int) == pts]["position"].astype(int).tolist()
        total_prize = sum(prize_map.get(p, 0.0) for p in tied)
        prize = (total_prize / len(tied)) if total_prize else 0.0
        out.append({
            "Season": sid,
            "Position": pos,
            "Points": pts,
            "Prize": prize,
        })
    return pd.DataFrame(out).sort_values("Season")


@st.cache_data(ttl=300)
def get_best_player_season(
    manager_name: str,
    selections_df: pd.DataFrame,
    goals_df: pd.DataFrame,
    players_df: pd.DataFrame,
    seasons_df: pd.DataFrame,
) -> tuple[str, str, int]:
    """Returns (player_name, season_id, pts) for the best single-season outfield player."""
    mgr_sels = selections_df[
        (selections_df["manager_name"] == manager_name) &
        (selections_df["position"].str.upper() != "GK")
    ]
    if mgr_sels.empty:
        return ("—", "", 0)

    player_names = players_df.set_index("code")["friendly_name"].to_dict()
    last_gw_map = {
        r["season_id"]: int(r["last_gw_synced"]) if str(r.get("last_gw_synced", "")) not in ("", "nan") else 38
        for _, r in seasons_df.iterrows()
    }
    best: tuple[str, str, int] = ("—", "", 0)

    for _, s in mgr_sels.iterrows():
        code = s["player_code"]
        pos = s["position"].upper()
        season_id = s["season_id"]
        gw_from = _int(s["gw_from"])
        gw_to_raw = s.get("gw_to", "")
        last_gw = last_gw_map.get(season_id, 38)
        gw_to = last_gw if str(gw_to_raw) in ("", "nan") or pd.isna(gw_to_raw) else min(_int(gw_to_raw), last_gw)

        name = _code_display_name(code, player_names)

        g_rows = goals_df[
            (goals_df["player_code"] == code) &
            (goals_df["season_id"] == season_id) &
            (goals_df["game_week"].astype(int) >= gw_from) &
            (goals_df["game_week"].astype(int) <= gw_to)
        ]
        total_goals = int(g_rows["goals_scored"].astype(int).sum()) if not g_rows.empty else 0
        pts = int(score_outfield_player(pos, total_goals))
        if pts > best[2]:
            best = (name, season_id, pts)

    return best


@st.cache_data(ttl=300)
def get_favourite_club(
    manager_name: str,
    selections_df: pd.DataFrame,
    players_df: pd.DataFrame,
) -> tuple[str, int]:
    """Returns (club_name, count) for the club most selected from across all seasons."""
    mgr_sels = selections_df[
        (selections_df["manager_name"] == manager_name) &
        (selections_df["position"].str.upper() != "GK")
    ]
    if mgr_sels.empty:
        return ("—", 0)

    code_to_team_id = players_df.set_index("code")["team_id"].to_dict()
    team_name_lookup: dict[tuple, str] = {
        (r["season"], str(r["element_id"])): r["friendly_name"]
        for _, r in players_df[players_df["type"] == "team"].iterrows()
    }

    club_counts: dict[str, int] = {}
    for _, s in mgr_sels.iterrows():
        code = s["player_code"]
        season_id = s["season_id"]
        team_id = str(code_to_team_id.get(code, "") or "")
        if team_id:
            club = team_name_lookup.get((season_id, team_id), "")
        elif "-player-" in code:
            suffix = code.split("-player-", 1)[-1]
            club = _norm_team_suffix(suffix.rsplit(" - ", 1)[-1].strip()) if " - " in suffix else ""
        else:
            club = ""
        if club:
            club_counts[club] = club_counts.get(club, 0) + 1

    if not club_counts:
        return ("—", 0)
    best = max(club_counts, key=club_counts.__getitem__)
    return (best, club_counts[best])


@st.cache_data(ttl=300)
def get_top_player_per_season(
    manager_name: str,
    selections_df: pd.DataFrame,
    goals_df: pd.DataFrame,
    players_df: pd.DataFrame,
    seasons_df: pd.DataFrame,
) -> dict[str, tuple[str, int, str]]:
    """Returns {season_id: (player_name, pts, player_code)} for the top outfield player each season."""
    mgr_sels = selections_df[
        (selections_df["manager_name"] == manager_name) &
        (selections_df["position"].str.upper() != "GK")
    ]
    if mgr_sels.empty:
        return {}

    player_names = players_df.set_index("code")["friendly_name"].to_dict()
    last_gw_map = {
        r["season_id"]: int(r["last_gw_synced"]) if str(r.get("last_gw_synced", "")) not in ("", "nan") else 38
        for _, r in seasons_df.iterrows()
    }
    season_bests: dict[str, tuple[str, int, str]] = {}

    for _, s in mgr_sels.iterrows():
        code = s["player_code"]
        pos = s["position"].upper()
        season_id = s["season_id"]
        gw_from = _int(s["gw_from"])
        gw_to_raw = s.get("gw_to", "")
        last_gw = last_gw_map.get(season_id, 38)
        gw_to = last_gw if str(gw_to_raw) in ("", "nan") or pd.isna(gw_to_raw) else min(_int(gw_to_raw), last_gw)

        name = _code_display_name(code, player_names)

        g_rows = goals_df[
            (goals_df["player_code"] == code) &
            (goals_df["season_id"] == season_id) &
            (goals_df["game_week"].astype(int) >= gw_from) &
            (goals_df["game_week"].astype(int) <= gw_to)
        ]
        total_goals = int(g_rows["goals_scored"].astype(int).sum()) if not g_rows.empty else 0
        pts = int(score_outfield_player(pos, total_goals))
        cur = season_bests.get(season_id, ("—", 0, ""))
        if pts > cur[1]:
            season_bests[season_id] = (name, pts, code)

    return season_bests


@st.cache_data(ttl=300)
def get_most_loyal_player(
    manager_name: str,
    selections_df: pd.DataFrame,
    players_df: pd.DataFrame,
) -> tuple[str, int]:
    """Returns (player_name, n_seasons) for the outfield player selected in the most seasons."""
    mgr_sels = selections_df[
        (selections_df["manager_name"] == manager_name) &
        (selections_df["position"].str.upper() != "GK")
    ]
    if mgr_sels.empty:
        return ("—", 0)
    code_to_fpl = players_df.set_index("code")["_fpl_code"].to_dict()
    player_names = players_df.set_index("code")["friendly_name"].to_dict()
    fpl_seasons: dict[str, set] = {}
    fpl_name: dict[str, str] = {}
    for _, s in mgr_sels.iterrows():
        code = s["player_code"]
        fpl = code_to_fpl.get(code, "") or code
        fpl_seasons.setdefault(fpl, set()).add(s["season_id"])
        if fpl not in fpl_name:
            name = _code_display_name(code, player_names)
            fpl_name[fpl] = name
    if not fpl_seasons:
        return ("—", 0)
    best = max(fpl_seasons, key=lambda f: len(fpl_seasons[f]))
    return (fpl_name.get(best, "—"), len(fpl_seasons[best]))


def get_best_gameweek(manager_name: str, best_gameweeks_df: pd.DataFrame) -> tuple[str, int]:
    """Returns (label, pts) from the pre-computed best_gameweeks.csv."""
    if best_gameweeks_df.empty or "manager_name" not in best_gameweeks_df.columns:
        return ("—", 0)
    row = best_gameweeks_df[best_gameweeks_df["manager_name"] == manager_name]
    if row.empty:
        return ("—", 0)
    return (str(row.iloc[0]["label"]), int(row.iloc[0]["pts"]))


@st.cache_data(ttl=300)
def get_biggest_buy(
    manager_name: str,
    selections_df: pd.DataFrame,
    players_df: pd.DataFrame,
) -> tuple[str, int]:
    """Returns (player_name, cost) for the most expensive outfield player ever bought."""
    mgr_sels = selections_df[
        (selections_df["manager_name"] == manager_name) &
        (selections_df["position"].str.upper() != "GK")
    ]
    if mgr_sels.empty:
        return ("—", 0)
    player_names = players_df.set_index("code")["friendly_name"].to_dict()
    best_cost, best_name = 0, "—"
    for _, s in mgr_sels.iterrows():
        cost_raw = s.get("cost", "")
        if not cost_raw or str(cost_raw) in ("", "nan"):
            continue
        try:
            cost = float(cost_raw)
        except ValueError:
            continue
        if cost > best_cost:
            best_cost = cost
            code = s["player_code"]
            name = _code_display_name(code, player_names)
            best_name = name
    return (best_name, int(best_cost)) if best_cost else ("—", 0)


@st.cache_data(ttl=300)
def get_total_goals(
    manager_name: str,
    selections_df: pd.DataFrame,
    goals_df: pd.DataFrame,
) -> int:
    """Returns total outfield goals scored by this manager's players across all seasons."""
    mgr_sels = selections_df[
        (selections_df["manager_name"] == manager_name) &
        (selections_df["position"].str.upper() != "GK")
    ]
    if mgr_sels.empty or goals_df.empty:
        return 0
    total = 0
    for _, s in mgr_sels.iterrows():
        code = s["player_code"]
        season_id = s["season_id"]
        gw_from = _int(s["gw_from"])
        gw_to_raw = s.get("gw_to", "")
        g_rows = goals_df[
            (goals_df["player_code"] == code) &
            (goals_df["season_id"] == season_id) &
            (goals_df["game_week"].astype(str) != "0") &
            (goals_df["game_week"].astype(int) >= gw_from)
        ]
        if str(gw_to_raw) not in ("", "nan") and not pd.isna(gw_to_raw):
            g_rows = g_rows[g_rows["game_week"].astype(int) <= _int(gw_to_raw)]
        total += int(g_rows["goals_scored"].astype(int).sum()) if not g_rows.empty else 0
    return total


@st.cache_data(ttl=300)
def compute_stats_corner(
    selections_df: pd.DataFrame,
    goals_df: pd.DataFrame,
    overrides_df: pd.DataFrame,
    players_df: pd.DataFrame,
    seasons_df: pd.DataFrame,
    standings_df: pd.DataFrame,
) -> dict:
    player_names = players_df.set_index("code")["friendly_name"].to_dict()
    code_to_fpl = players_df.set_index("code")["_fpl_code"].to_dict()
    last_gw_map = {
        r["season_id"]: int(r["last_gw_synced"]) if str(r.get("last_gw_synced", "")) not in ("", "nan") else 38
        for _, r in seasons_df.iterrows()
    }
    MULT = {"DEF": 3, "MID": 2, "FWD": 1}

    def _pkey(code: str) -> str:
        fpl = code_to_fpl.get(code, "") or ""
        return fpl if fpl else code

    # ── Per-player-season outfield stats (vectorised) ────────────────────────
    outfield = selections_df[selections_df["position"].str.upper() != "GK"].copy()
    outfield["gw_from_n"] = outfield["gw_from"].apply(_int)
    outfield["cost_n"] = pd.to_numeric(outfield["cost"], errors="coerce").fillna(0.0)
    outfield["mult"] = outfield["position"].str.upper().map(MULT).fillna(1).astype(int)

    def _resolve_gw_to(row: "pd.Series") -> int:  # type: ignore[name-defined]
        last_gw = last_gw_map.get(row["season_id"], 38)
        raw = row.get("gw_to", "")
        if str(raw) in ("", "nan") or pd.isna(raw):
            return last_gw
        return min(_int(raw), last_gw)

    outfield["gw_to_n"] = outfield.apply(_resolve_gw_to, axis=1)

    g = goals_df[["player_code", "season_id", "game_week", "goals_scored"]].copy()
    g["game_week"] = g["game_week"].astype(int)
    g["goals_scored"] = g["goals_scored"].astype(int)

    sel_slim = outfield[["player_code", "season_id", "manager_name", "gw_from_n", "gw_to_n", "mult", "cost_n"]]
    merged_goals = sel_slim.merge(g, on=["player_code", "season_id"], how="left")
    in_range = merged_goals["game_week"].isna() | (
        (merged_goals["game_week"] >= merged_goals["gw_from_n"]) &
        (merged_goals["game_week"] <= merged_goals["gw_to_n"])
    )
    merged_goals = merged_goals[in_range].copy()
    merged_goals["pts"] = (merged_goals["goals_scored"].fillna(0) * merged_goals["mult"]).astype(int)

    # Base: all selections (so zero-goal players appear in agg with 0 pts)
    all_sel = outfield.groupby(["player_code", "season_id", "manager_name"]).agg(
        cost_n=("cost_n", "max")
    ).reset_index()
    pts_df = (
        merged_goals[merged_goals["game_week"].notna()]
        .groupby(["player_code", "season_id", "manager_name"])["pts"].sum()
        .reset_index()
    )
    agg = all_sel.merge(pts_df, on=["player_code", "season_id", "manager_name"], how="left")
    agg["pts"] = agg["pts"].fillna(0).astype(int)

    def _rec(row: "pd.Series") -> dict:  # type: ignore[name-defined]
        return {
            "player": _code_display_name(row["player_code"], player_names),
            "pts": int(row["pts"]),
            "cost": float(row["cost_n"]),
            "season": row["season_id"],
            "manager": row["manager_name"],
        }

    most_pts = _rec(agg.nlargest(1, "pts").iloc[0]) if not agg.empty else None
    most_expensive = _rec(agg.nlargest(1, "cost_n").iloc[0]) if not agg.empty else None

    costly = agg[agg["cost_n"] >= 5]
    biggest_flop = _rec(
        costly.sort_values(["pts", "cost_n"], ascending=[True, False]).iloc[0]
    ) if not costly.empty else None

    value = agg[(agg["cost_n"] >= 5) & (agg["pts"] >= 5)].copy()
    if not value.empty:
        value["ratio"] = value["pts"] / value["cost_n"]
        best_val_row = value.nlargest(1, "ratio").iloc[0]
        best_val: dict | None = _rec(best_val_row)
        best_val["ratio"] = round(float(best_val_row["ratio"]), 1)  # type: ignore[arg-type]
    else:
        best_val = None

    # ── Hat-trick hero ────────────────────────────────────────────────────────
    ht = goals_df[goals_df["goals_scored"].astype(int) >= 3].copy()
    hat_trick_hero: dict | None = None
    if not ht.empty:
        ht["pkey"] = ht["player_code"].map(_pkey)
        ht_counts = ht.groupby("pkey").size()
        best_ht_pkey = ht_counts.idxmax()
        best_ht_code = ht[ht["pkey"] == best_ht_pkey].iloc[0]["player_code"]
        hat_trick_hero = {
            "player": _code_display_name(best_ht_code, player_names),
            "count": int(ht_counts[best_ht_pkey]),
        }

    # ── Most selected player (unique manager-season pairs) ────────────────────
    outfield["pkey"] = outfield["player_code"].map(_pkey)
    sel_counts = (
        outfield.drop_duplicates(subset=["pkey", "manager_name", "season_id"])
        .groupby("pkey").size()
    )
    most_selected: dict | None = None
    if not sel_counts.empty:
        best_sel_pkey = sel_counts.idxmax()
        best_sel_code = outfield[outfield["pkey"] == best_sel_pkey].iloc[0]["player_code"]
        most_selected = {
            "player": _code_display_name(best_sel_code, player_names),
            "count": int(sel_counts[best_sel_pkey]),
        }

    # ── Transfer regret (player dropped, then scored big the next season) ─────
    agg["pkey"] = agg["player_code"].map(_pkey)
    agg["season_year"] = pd.to_numeric(
        agg["season_id"].str.extract(r"^(\d{4})", expand=False), errors="coerce"
    ).fillna(0).astype(int)

    transfer_regret: dict | None = None
    best_regret_pts = 0
    for pkey_val, grp in agg.groupby("pkey"):
        grp_s = grp.sort_values("season_year").to_dict("records")
        for i in range(1, len(grp_s)):
            prev, curr = grp_s[i - 1], grp_s[i]
            if (
                curr["season_year"] == prev["season_year"] + 1
                and curr["manager_name"] != prev["manager_name"]
                and curr["pts"] > best_regret_pts
            ):
                best_regret_pts = curr["pts"]
                transfer_regret = {
                    "player": _code_display_name(curr["player_code"], player_names),
                    "pts": int(curr["pts"]),
                    "season": curr["season_id"],
                    "manager": curr["manager_name"],
                    "prev_manager": prev["manager_name"],
                    "cost": float(curr["cost_n"]),
                }

    # ── Highest season score (manager total) ──────────────────────────────────
    highest_season_score: dict | None = None
    if not standings_df.empty and "total_points" in standings_df.columns:
        std = standings_df.copy()
        std["total_points"] = pd.to_numeric(std["total_points"], errors="coerce")
        if std["total_points"].notna().any():
            r = std.nlargest(1, "total_points").iloc[0]
            highest_season_score = {
                "manager": r["manager_name"],
                "pts": int(r["total_points"]),  # type: ignore[arg-type]
                "season": r["season_id"],
            }

    # ── Highest scoring gameweek (sum across all squads) ─────────────────────
    highest_scoring_gw: dict | None = None
    gw_out = merged_goals[merged_goals["game_week"].notna()].copy()
    gw_outfield = (
        gw_out.groupby(["season_id", "game_week"])["pts"].sum()
        if not gw_out.empty else pd.Series(dtype=float)
    )
    if not overrides_df.empty:
        gk_sels = selections_df[selections_df["position"].str.upper() == "GK"].copy()
        gk_sels["gw_from_n"] = gk_sels["gw_from"].apply(_int)
        gk_sels["gw_to_n"] = gk_sels.apply(_resolve_gw_to, axis=1)
        ov = overrides_df[["player_code", "season_id", "game_week", "override_points"]].copy()
        ov["game_week"] = ov["game_week"].astype(int)
        ov["override_points"] = ov["override_points"].astype(int)
        gk_m = ov.merge(gk_sels[["player_code", "season_id", "gw_from_n", "gw_to_n"]], on=["player_code", "season_id"])
        gk_m = gk_m[(gk_m["game_week"] >= gk_m["gw_from_n"]) & (gk_m["game_week"] <= gk_m["gw_to_n"])]
        gw_gk = gk_m.groupby(["season_id", "game_week"])["override_points"].sum()
    else:
        gw_gk = pd.Series(dtype=float)
    gw_total = gw_outfield.add(gw_gk, fill_value=0)
    if not gw_total.empty:
        _gw_argmax = int(gw_total.values.argmax())  # type: ignore[union-attr]
        _gw_idx = gw_total.index[_gw_argmax]
        highest_scoring_gw = {
            "season": _gw_idx[0], "gw": int(_gw_idx[1]), "total_pts": int(gw_total.iloc[_gw_argmax])  # type: ignore[index]
        }

    # ── Biggest season-to-season improvement ─────────────────────────────────
    biggest_improvement: dict | None = None
    if not standings_df.empty and "position" in standings_df.columns:
        std2 = standings_df.copy()
        std2["pos_num"] = pd.to_numeric(std2["position"], errors="coerce")
        std2["season_year"] = pd.to_numeric(
            std2["season_id"].str.extract(r"^(\d{4})", expand=False), errors="coerce"
        ).fillna(0).astype(int)
        std2 = std2.dropna(subset=["pos_num"]).sort_values(["manager_name", "season_year"])
        std2["prev_pos"] = std2.groupby("manager_name")["pos_num"].shift(1)
        std2["prev_year"] = std2.groupby("manager_name")["season_year"].shift(1)
        std2["improvement"] = std2["prev_pos"] - std2["pos_num"]
        consec = std2[std2["season_year"] == std2["prev_year"] + 1].dropna(subset=["improvement"])
        if not consec.empty:
            br = consec.nlargest(1, "improvement").iloc[0]
            biggest_improvement = {
                "manager": br["manager_name"],
                "from_pos": int(br["prev_pos"]),  # type: ignore[arg-type]
                "to_pos": int(br["pos_num"]),  # type: ignore[arg-type]
                "improvement": int(br["improvement"]),  # type: ignore[arg-type]
                "season": br["season_id"],
            }

    # ── Manager leaderboards ──────────────────────────────────────────────────
    most_wins: list[tuple[str, int]] = []
    most_prizes: list[tuple[str, int]] = []
    avg_position: list[tuple[str, float, int]] = []
    unluckiest: list[tuple[str, int]] = []
    most_seasons_list: list[tuple[str, int]] = []

    if not standings_df.empty:
        std3 = standings_df.copy()
        std3["pos_num"] = pd.to_numeric(std3["position"], errors="coerce")
        if "position" in std3.columns:
            wins = std3[std3["position"].astype(str) == "1"]["manager_name"].value_counts()
            most_wins = [(str(k), int(v)) for k, v in wins.head(5).items()]
            fourth = std3[std3["position"].astype(str) == "4"]["manager_name"].value_counts()
            unluckiest = [(str(k), int(v)) for k, v in fourth.head(5).items()]
            mgr_avg = std3.dropna(subset=["pos_num"]).groupby("manager_name").agg(
                avg_pos=("pos_num", "mean"), n=("pos_num", "count")
            ).reset_index()
            mgr_avg = mgr_avg[mgr_avg["n"] >= 3].sort_values("avg_pos").head(5)
            avg_position = [
                (r["manager_name"], round(float(r["avg_pos"]), 1), int(r["n"]))
                for _, r in mgr_avg.iterrows()
            ]
        if "prize" in std3.columns:
            prize_rows = std3[std3["prize"].fillna("").astype(str).str.strip().str.replace(".0", "", regex=False) != ""]
            most_prizes = [(str(k), int(v)) for k, v in prize_rows["manager_name"].value_counts().head(5).items()]

    most_seasons_s = selections_df.groupby("manager_name")["season_id"].nunique().sort_values(ascending=False).head(5)
    most_seasons_list = [(str(k), int(v)) for k, v in most_seasons_s.items()]

    return {
        "most_expensive": most_expensive,
        "most_pts_season": most_pts,
        "biggest_flop": biggest_flop,
        "best_value": best_val,
        "hat_trick_hero": hat_trick_hero,
        "most_selected_player": most_selected,
        "transfer_regret": transfer_regret,
        "highest_season_score": highest_season_score,
        "highest_scoring_gw": highest_scoring_gw,
        "biggest_improvement": biggest_improvement,
        "most_wins": most_wins,
        "most_prizes": most_prizes,
        "avg_position": avg_position,
        "unluckiest": unluckiest,
        "most_seasons": most_seasons_list,
    }


@st.cache_data(ttl=300)
def player_gw_points(
    player_code: str,
    position: str,
    season_id: str,
    up_to_gw: int,
    gw_from: int,
    gw_to: int,
    goals_df: pd.DataFrame,
    players_df: pd.DataFrame,
    overrides_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    pos = position.upper()
    rows = []
    for gw in range(gw_from, min(gw_to, up_to_gw) + 1):
        # Override takes precedence over normal calculation
        if overrides_df is not None and not overrides_df.empty:
            ov = overrides_df[
                (overrides_df["player_code"] == player_code)
                & (overrides_df["season_id"] == season_id)
                & (overrides_df["game_week"].astype(int) == gw)
            ]
            if not ov.empty:
                pts = int(ov.iloc[0]["override_points"])
                if pos == "GK":
                    rows.append({"GW": gw, "Goals Conceded": 0, "Pts": pts})
                else:
                    rows.append({"GW": gw, "Goals": 0, "Pts": pts})
                continue

        if pos == "GK":
            conceded = _goals_for(goals_df, player_code, season_id, gw, "goals_conceded")
            team_id = _int(player_code.split("-")[-1])
            gk_codes = _gk_player_codes_for_team(players_df, team_id, season_id)
            gk_goals = sum(_goals_for(goals_df, c, season_id, gw, "goals_scored") for c in gk_codes)
            pts = score_gk_team(conceded, gk_goals)
            rows.append({"GW": gw, "Goals Conceded": conceded, "Pts": pts})
        else:
            goals = _goals_for(goals_df, player_code, season_id, gw, "goals_scored")
            pts = score_outfield_player(pos, goals)
            rows.append({"GW": gw, "Goals": goals, "Pts": pts})
    return pd.DataFrame(rows)


@st.cache_data(ttl=300)
def manager_squad_points(
    manager_name: str,
    season_id: str,
    up_to_gw: int,
    selections_df: pd.DataFrame,
    goals_df: pd.DataFrame,
    players_df: pd.DataFrame,
    overrides_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    sel = selections_df[
        (selections_df["manager_name"] == manager_name) &
        (selections_df["season_id"] == season_id)
    ]
    if sel.empty:
        return pd.DataFrame()

    player_names = players_df.set_index("code")["friendly_name"].to_dict()
    gkp_by_team: dict[int, list[str]] = {}
    for _, r in players_df[
        (players_df["season"] == season_id) &
        (players_df["type"] == "player") &
        (players_df["fpl_position"] == "GKP")
    ].iterrows():
        if r["team_id"]:
            gkp_by_team.setdefault(int(r["team_id"]), []).append(r["code"])

    rows = []
    for _, s in sel.iterrows():
        code = s["player_code"]
        pos = s["position"].upper()
        gw_from = _int(s["gw_from"])
        gw_to_raw = s.get("gw_to", "")
        gw_to = up_to_gw if (str(gw_to_raw) == "" or pd.isna(gw_to_raw)) else min(_int(gw_to_raw), up_to_gw)
        name = _code_display_name(code, player_names)

        for gw in range(gw_from, gw_to + 1):
            # Override takes precedence
            if overrides_df is not None and not overrides_df.empty:
                ov = overrides_df[
                    (overrides_df["player_code"] == code)
                    & (overrides_df["season_id"] == season_id)
                    & (overrides_df["game_week"].astype(int) == gw)
                ]
                if not ov.empty:
                    pts = int(ov.iloc[0]["override_points"])
                    if pos == "GK":
                        rows.append({"Player": name, "Pos": pos, "GW": gw, "Goals Conceded": 0, "Pts": pts})
                    else:
                        rows.append({"Player": name, "Pos": pos, "GW": gw, "Goals": 0, "Pts": pts})
                    continue

            if pos == "GK":
                conceded = _goals_for(goals_df, code, season_id, gw, "goals_conceded")
                team_id = _int(code.split("-")[-1])
                gk_goals = sum(_goals_for(goals_df, c, season_id, gw, "goals_scored") for c in gkp_by_team.get(team_id, []))
                pts = score_gk_team(conceded, gk_goals)
                rows.append({"Player": name, "Pos": pos, "GW": gw, "Goals Conceded": conceded, "Pts": pts})
            else:
                goals = _goals_for(goals_df, code, season_id, gw, "goals_scored")
                pts = score_outfield_player(pos, goals)
                rows.append({"Player": name, "Pos": pos, "GW": gw, "Goals": goals, "Pts": pts})

    return pd.DataFrame(rows)


# ── Render ─────────────────────────────────────────────────────────────────────

data = load_data()
seasons_df = data["seasons"]
standings_df = data["standings"]
prizes_df = data["prizes"]
players_df = data["players"].copy()
# Stable cross-season player identity: prefer fpl_permanent_code column (set by both
# FPL sync and vaastav sync), fall back to parsing photo_url for any legacy rows.
import re as _re_global
_photo_re = _re_global.compile(r"/(\d+)\.png$")
def _derive_fpl_code(row: "pd.Series") -> str:  # type: ignore[name-defined]
    perm = str(row.get("fpl_permanent_code", "") or "").strip()
    if perm and perm not in ("0", "nan"):
        return perm
    url = str(row.get("photo_url", "") or "")
    m = _photo_re.search(url)
    return m.group(1) if m else ""
players_df["_fpl_code"] = players_df.apply(_derive_fpl_code, axis=1)
all_season_ids = sorted(seasons_df["season_id"].tolist(), reverse=True)
# show_in_dashboard="false" hides a season from filter dropdowns (player history still shows all)
_show = seasons_df.get("show_in_dashboard", pd.Series(dtype=str))
if "show_in_dashboard" in seasons_df.columns:
    _visible = seasons_df[seasons_df["show_in_dashboard"].fillna("").str.strip().str.lower() != "false"]["season_id"].tolist()
else:
    _visible = seasons_df["season_id"].tolist()
season_options = sorted(_visible, reverse=True)

_teams_df = players_df[players_df["type"] == "team"]
team_badge_lookup: dict[str, str] = {}
for _, _tr in _teams_df.iterrows():
    _tc = str(_tr.get("team_code", ""))
    _tn = str(_tr.get("friendly_name", ""))
    if _tn and _tc and _tc not in ("", "nan") and _tn not in team_badge_lookup:
        team_badge_lookup[_tn] = f"https://resources.premierleague.com/premierleague/badges/t{_tc}.png"

_hist_badges = _load_historical_badges(Path(get_settings().data_dir))
for _hn, _hu in _hist_badges.items():
    if _hn not in team_badge_lookup:
        team_badge_lookup[_hn] = _hu

col_title, col_updated = st.columns([3, 1])
col_title.title("⚽ Select Fantasy Football Auction")
if data.get("last_updated"):
    col_updated.caption(f"Last updated: {data['last_updated']}")

# Handle navigation from links (?manager=X&season=Y  or  ?player=<fpl_code>)
# Set widget keys directly here — before st.rerun() — so the fresh key has
# never been "owned" by a prior widget render and Streamlit will honour it.
_qp = st.query_params
if "manager" in _qp:
    _mgr_name = _qp.get("manager", "")
    _szn_qp = _qp.get("season", "")
    _table_szn = _qp.get("table_season", "")
    if _mgr_name:
        st.session_state["mgr_select"] = _mgr_name
    if _szn_qp and _szn_qp in season_options:
        st.session_state["mgr_season"] = _szn_qp
    if _table_szn and _table_szn in season_options:
        _lt_new_gen = st.session_state.get("_lt_szn_gen", 0) + 1
        st.session_state["_lt_szn_gen"] = _lt_new_gen
        for _k in [k for k in st.session_state if k.startswith("lt_season_sel_")]:
            del st.session_state[_k]
        st.session_state[f"lt_season_sel_{_lt_new_gen}"] = _table_szn
    st.session_state["_auto_nav_managers"] = True
    st.query_params.clear()
    st.rerun()
if "player" in _qp:
    st.session_state["_pending_player_fpl_code"] = _qp.get("player", "")
    st.session_state["_auto_nav_players"] = True
    _mgr_qp = _qp.get("mgr", "")
    _mgr_szn_qp = _qp.get("mgr_season", "")
    _table_szn_qp = _qp.get("table_season", "")
    if _mgr_qp:
        st.session_state["mgr_select"] = _mgr_qp
    if _mgr_szn_qp and _mgr_szn_qp in season_options:
        st.session_state["mgr_season"] = _mgr_szn_qp
    if _table_szn_qp and _table_szn_qp in season_options:
        _lt_new_gen = st.session_state.get("_lt_szn_gen", 0) + 1
        st.session_state["_lt_szn_gen"] = _lt_new_gen
        for _k in [k for k in st.session_state if k.startswith("lt_season_sel_")]:
            del st.session_state[_k]
        st.session_state[f"lt_season_sel_{_lt_new_gen}"] = _table_szn_qp
    st.query_params.clear()
    st.rerun()

_lt_szn_gen = st.session_state.get("_lt_szn_gen", 0)
_mgr_szn_key = "mgr_season"

tab_table, tab_managers, tab_players, tab_stats = st.tabs(["League Table", "Managers", "Players", "Trophy Cabinet"])

_is_autonav_mgr = st.session_state.pop("_auto_nav_managers", False)
_is_autonav_pl = st.session_state.pop("_auto_nav_players", False)

if _is_autonav_mgr:
    import streamlit.components.v1 as _comp_qp
    _comp_qp.html(
        '<script>setTimeout(function(){window.parent.document.querySelectorAll(\'[data-baseweb="tab"]\')[1].click();},200);</script>',
        height=0,
    )
if _is_autonav_pl:
    import streamlit.components.v1 as _comp_qp2
    _comp_qp2.html(
        '<script>setTimeout(function(){window.parent.document.querySelectorAll(\'[data-baseweb="tab"]\')[2].click();},200);</script>',
        height=0,
    )


# ══ Tab 1: League Table ════════════════════════════════════════════════════════

with tab_table:
    season_id = st.selectbox("Season", season_options, key=f"lt_season_sel_{_lt_szn_gen}") or season_options[0]
    _szn_row = seasons_df[seasons_df["season_id"] == season_id]
    _end_date_raw = _szn_row.iloc[0].get("end_date", "") if not _szn_row.empty else ""
    _last_gw_raw = _szn_row.iloc[0].get("last_gw_synced", "") if not _szn_row.empty else ""
    if _end_date_raw and str(_end_date_raw) not in ("", "nan"):
        from datetime import date as _date
        _season_finished = _date.fromisoformat(str(_end_date_raw)) < _date.today()
    else:
        _season_finished = str(_last_gw_raw) == "38"
    render_standings_table(standings_df, prizes_df, season_id, season_finished=_season_finished)


# ══ Tab 2: Managers ═══════════════════════════════════════════════════════════

with tab_managers:
    all_managers = get_all_managers(data)
    if not all_managers:
        st.info("No manager data available yet.")
    else:
        col_mgr, col_szn = st.columns(2)
        selected_manager = col_mgr.selectbox("Manager", all_managers, key="mgr_select")
        current_season_id = col_szn.selectbox("Season", season_options, key=_mgr_szn_key) or season_options[0]

        current_season_row = seasons_df[seasons_df["season_id"] == current_season_id].iloc[0]
        current_last_gw_raw = current_season_row.get("last_gw_synced", "")
        current_last_gw = int(current_last_gw_raw) if str(current_last_gw_raw) not in ("", "nan") else 0

        current_standing = None
        if not standings_df.empty and "manager_name" in standings_df.columns:
            curr_rows = standings_df[
                (standings_df["season_id"] == current_season_id) &
                (standings_df["manager_name"] == selected_manager)
            ]
            if not curr_rows.empty:
                r = curr_rows.iloc[0]
                pos = int(r["position"])
                pts = int(float(r["total_points"]))
                _prize_map = {
                    int(p["position"]): float(p["prize_amount"])
                    for _, p in prizes_df[prizes_df["season_id"] == current_season_id].iterrows()
                }
                # Resolve shared prizes for tied managers
                _season_rows = standings_df[standings_df["season_id"] == current_season_id]
                _pts_to_pos: dict[int, list[int]] = {}
                for _, sr in _season_rows.iterrows():
                    _p = int(float(sr["total_points"]))
                    _pts_to_pos.setdefault(_p, []).append(int(sr["position"]))
                _tied_positions = _pts_to_pos.get(pts, [pos])
                _total_prize = sum(_prize_map.get(p, 0.0) for p in _tied_positions)
                prize = (_total_prize / len(_tied_positions)) if _total_prize else None
                _is_shared = prize is not None and len(_tied_positions) > 1
                current_standing = {"pos": pos, "pts": pts, "prize": prize, "shared": _is_shared}

        _mgr_szn_row = seasons_df[seasons_df["season_id"] == current_season_id]
        _mgr_end_raw = _mgr_szn_row.iloc[0].get("end_date", "") if not _mgr_szn_row.empty else ""
        _mgr_lgw_raw = _mgr_szn_row.iloc[0].get("last_gw_synced", "") if not _mgr_szn_row.empty else ""
        if _mgr_end_raw and str(_mgr_end_raw) not in ("", "nan"):
            from datetime import date as _date
            _mgr_szn_finished = _date.fromisoformat(str(_mgr_end_raw)) < _date.today()
        else:
            _mgr_szn_finished = str(_mgr_lgw_raw) == "38"

        if current_standing:
            _szn_all = standings_df[standings_df["season_id"] == current_season_id] if not standings_df.empty else pd.DataFrame()
            _top_pts_mgr = int(_szn_all["total_points"].astype(float).max()) if not _szn_all.empty else 0
            _is_champion = _mgr_szn_finished and current_standing["pts"] == _top_pts_mgr
            _pos_label = _ordinal(current_standing['pos']) + (" 🏆" if _is_champion else "")
            c1, c2, c3 = st.columns(3)
            c1.metric("Position", _pos_label)
            c2.metric("Points", current_standing["pts"])
            _prize_display = (
                (f"£{current_standing['prize']:.0f} ea" if current_standing["shared"] else f"£{current_standing['prize']:.0f}")
                if current_standing["prize"] else "—"
            )
            c3.metric("Prize", _prize_display)
        else:
            st.info("No standings data for this season.")

        # Current squad + GW breakdown
        mgr_sels = data["selections"][data["selections"]["manager_name"] == selected_manager] if not data["selections"].empty else pd.DataFrame()
        current_sels = mgr_sels[mgr_sels["season_id"] == current_season_id] if not mgr_sels.empty else pd.DataFrame()

        if current_sels.empty:
            st.info("No squad selections loaded for this season yet.")
        else:
            breakdown = pd.DataFrame()
            if current_last_gw:
                breakdown = manager_squad_points(
                    selected_manager, current_season_id, current_last_gw,
                    data["selections"], data["goals"], players_df,
                    data.get("overrides"),
                )

            player_names_map = players_df.set_index("code")["friendly_name"].to_dict()
            code_to_fpl_code = players_df.set_index("code")["_fpl_code"].to_dict()
            # Build lookups for team name and status keyed by player code
            player_details = players_df[
                (players_df["season"] == current_season_id) & (players_df["type"] == "player")
            ].set_index("code")[["team_id", "status", "news"]].to_dict("index")
            team_names_map = players_df[
                (players_df["season"] == current_season_id) & (players_df["type"] == "team")
            ].set_index("element_id")["friendly_name"].to_dict()
            _status_labels = {"I": "Injured", "D": "Doubtful", "S": "Suspended", "U": "Unavailable", "N": "N/A"}

            _pos_order = {"GK": 0, "DEF": 1, "MID": 2, "FWD": 3}
            squad_rows = []
            _seen_sq_codes: set[str] = set()
            for _, s in current_sels.iterrows():
                code = s["player_code"]
                _fpl_dedup = code_to_fpl_code.get(code, "") or code
                if _fpl_dedup in _seen_sq_codes:
                    continue
                _seen_sq_codes.add(_fpl_dedup)
                pos = s["position"].upper()
                cost = s.get("cost", "")
                details = player_details.get(code, {})
                team_id = str(details.get("team_id", "") or "")
                team_name = _code_team_name(code, pos, team_id, team_names_map, player_names_map)
                raw_news = details.get("news", "")
                raw_status = details.get("status", "A")
                status_str = raw_news if raw_news and str(raw_news) not in ("", "nan") else _status_labels.get(raw_status, "")
                gw_from_s = s.get("gw_from", "")
                gw_to_s = s.get("gw_to", "")
                gw_range = ""
                if str(gw_from_s) not in ("", "nan") and str(gw_to_s) not in ("", "nan"):
                    gw_range = f"GW{int(float(gw_from_s))}–{int(float(gw_to_s))}"
                elif str(gw_from_s) not in ("", "nan"):
                    gw_range = f"GW{int(float(gw_from_s))}+"
                squad_rows.append({
                    "Player": _code_display_name(code, player_names_map),
                    "Pos": pos,
                    "Team": team_name,
                    "Cost": f"£{float(cost):.0f}" if cost and str(cost) not in ("", "nan") else "—",
                    "GWs": gw_range,
                    "Status": status_str if str(status_str) not in ("", "nan") else "",
                    "_fpl_code": code_to_fpl_code.get(code, ""),
                })
            squad_df = pd.DataFrame(squad_rows)
            squad_df["_sort"] = squad_df["Pos"].map(_pos_order).fillna(99)
            squad_df = squad_df.sort_values("_sort").drop(columns="_sort")

            if not breakdown.empty:
                totals = breakdown.groupby("Player")["Pts"].sum().reset_index().rename(columns={"Pts": "Points"})
                _bd_g = breakdown.copy()
                if "Goals" in _bd_g.columns and "Goals Conceded" in _bd_g.columns:
                    _bd_g["_g"] = _bd_g["Goals"].fillna(_bd_g["Goals Conceded"]).fillna(0)
                elif "Goals" in _bd_g.columns:
                    _bd_g["_g"] = _bd_g["Goals"].fillna(0)
                elif "Goals Conceded" in _bd_g.columns:
                    _bd_g["_g"] = _bd_g["Goals Conceded"].fillna(0)
                else:
                    _bd_g["_g"] = 0
                goal_totals = _bd_g.groupby("Player")["_g"].sum().reset_index().rename(columns={"_g": "Goals"})
                totals = totals.merge(goal_totals, on="Player", how="left")
                squad_df = squad_df.merge(totals, on="Player", how="left").fillna({"Points": 0, "Goals": 0})
                squad_df["Points"] = squad_df["Points"].astype(int)
                squad_df["Goals"] = squad_df["Goals"].astype(int)

            if "Points" in squad_df.columns:
                total_row = {col: "" for col in squad_df.columns}
                total_row["Player"] = "Total"
                _cost_sum = squad_df["Cost"].str.replace("£", "", regex=False).apply(pd.to_numeric, errors="coerce").sum()
                total_row["Cost"] = f"£{int(_cost_sum)}" if pd.notna(_cost_sum) else ""
                total_row["Goals"] = str(int(squad_df["Goals"].sum())) if "Goals" in squad_df.columns else ""
                total_row["Points"] = str(int(squad_df["Points"].sum()))
                squad_df = pd.concat([squad_df, pd.DataFrame([total_row])], ignore_index=True)
                squad_df = squad_df[["Player", "Pos", "Team", "Cost", "GWs", "Goals", "Points", "Status", "_fpl_code"]]
            else:
                squad_df = squad_df[["Player", "Pos", "Team", "Cost", "GWs", "Status", "_fpl_code"]]

            st.markdown(f"**{current_season_id} Squad**")
            _pos_colours = {"GK": "#6366f1", "DEF": "#2563eb", "MID": "#22c55e", "FWD": "#f59e0b"}
            has_pts = "Points" in squad_df.columns
            has_goals = "Goals" in squad_df.columns
            sq_rows_html = []
            for i, (_, row) in enumerate(squad_df.iterrows()):
                is_total = row["Player"] == "Total"
                has_status = str(row.get("Status", "")) not in ("", "nan")
                row_style = "background:rgba(128,128,128,0.06);" if i % 2 == 1 else ""
                bold = "font-weight:bold;" if is_total else ""
                td = f"padding:6px 12px;{bold}"
                pos = str(row.get("Pos", ""))
                pc = _pos_colours.get(pos, "")
                pos_html = f'<span style="color:{pc};font-weight:600">{pos}</span>' if pc and not is_total else pos
                _fpl_c = str(row.get("_fpl_code", ""))
                if _fpl_c and not is_total:
                    _href = (
                        f"?player={urllib.parse.quote(_fpl_c)}"
                        f"&mgr={urllib.parse.quote(selected_manager)}"
                        f"&mgr_season={urllib.parse.quote(current_season_id)}"
                        f"&table_season={urllib.parse.quote(season_id)}"
                    )
                    player_cell = (
                        f'<a href="{_href}" target="_self" '
                        f'style="color:#60a5fa;text-decoration:none">{row["Player"]}</a>'
                    )
                else:
                    player_cell = row["Player"]
                _team_name = str(row["Team"])
                _badge = team_badge_lookup.get(_team_name, "")
                _badge_img = (
                    f'<img src="{_badge}" height="14" style="vertical-align:middle;margin-right:5px;border-radius:2px;filter:brightness(0.75)">'
                    if _badge and not is_total else ""
                )
                cells = [
                    f'<td style="{td}">{player_cell}</td>',
                    f'<td style="{td}">{pos_html}</td>',
                    f'<td style="{td}">{_badge_img}{_team_name}</td>',
                    f'<td style="{td}">{row["Cost"]}</td>',
                    f'<td style="{td}">{row["GWs"]}</td>',
                ]
                if has_goals:
                    cells.append(f'<td style="{td}text-align:right">{row["Goals"]}</td>')
                if has_pts:
                    cells.append(f'<td style="{td}text-align:right">{row["Points"]}</td>')
                status_display = str(row.get("Status", ""))
                status_text = status_display if status_display not in ("", "nan") else ""
                if status_text:
                    cells.append(f'<td class="sq-alert" style="{td}color:#fca5a5;">{status_text}</td>')
                else:
                    cells.append(f'<td style="{td}"></td>')
                sq_rows_html.append(f'<tr style="{row_style}">{"".join(cells)}</tr>')
            goals_th = '<th style="text-align:right">Goals</th>' if has_goals else ''
            pts_th = '<th style="text-align:right">Points</th>' if has_pts else ''
            sq_html = (
                '<style>'
                'table.sq{width:100%;border-collapse:collapse;font-size:0.92em}'
                'table.sq th{padding:6px 12px;text-align:left;border-bottom:2px solid #e5e7eb;'
                'color:#6b7280;font-weight:600;font-size:0.75em;text-transform:uppercase;letter-spacing:0.04em}'
                'table.sq tr:hover td{background:rgba(128,128,128,0.1)!important}'
                'table.sq td.sq-alert{background:#7f1d1d!important}'
                'table.sq tr:hover td.sq-alert{background:#991b1b!important}'
                '</style>'
                '<table class="sq"><thead><tr>'
                f'<th>Player</th><th>Pos</th><th>Team</th><th>Cost</th><th>GWs</th>{goals_th}{pts_th}<th>Status</th>'
                f'</tr></thead><tbody>{"".join(sq_rows_html)}</tbody></table>'
            )
            st.markdown(sq_html, unsafe_allow_html=True)

            if not breakdown.empty:
                gw_pts = breakdown.groupby("GW")["Pts"].sum().reset_index().sort_values("GW")
                gw_pts["Total"] = gw_pts["Pts"].cumsum()
                import altair as alt
                st.markdown("**Cumulative points**")
                _cum_chart = (
                    alt.Chart(gw_pts)
                    .mark_line(color="#22c55e")
                    .encode(
                        x=alt.X("GW:Q", axis=alt.Axis(tickMinStep=1)),
                        y=alt.Y("Total:Q"),
                    )
                    .properties(height=200)
                )
                st.altair_chart(_cum_chart, use_container_width=True)

        # ── Season history ────────────────────────────────────────────────────
        history_df = get_manager_history(standings_df, prizes_df, selected_manager)
        if not history_df.empty:
            st.divider()
            st.subheader("Season History")

            h1, h2, h3, h4 = st.columns(4)
            h1.metric("Seasons Managed", len(history_df))
            h2.metric("Best Finish", _ordinal(int(history_df['Position'].min())))
            h3.metric("Avg Finish", _ordinal(round(history_df['Position'].mean())))
            _prize_finishes = int((history_df['Prize'] > 0).sum())
            h4.metric("Prize Finishes", str(_prize_finishes) if _prize_finishes else "—")

            _loyal_name, _loyal_seasons = get_most_loyal_player(selected_manager, data["selections"], players_df)
            _loyal_label = f"{_loyal_name} — {_loyal_seasons} {'season' if _loyal_seasons == 1 else 'seasons'}" if _loyal_name != "—" else "—"
            _best_gw_label, _best_gw_pts = get_best_gameweek(selected_manager, data["best_gameweeks"])
            _best_gw_display = f"{_best_gw_label} — {_best_gw_pts}pts" if _best_gw_label != "—" else "—"
            _big_buy_name, _big_buy_cost = get_biggest_buy(selected_manager, data["selections"], players_df)
            _big_buy_display = f"{_big_buy_name} — £{_big_buy_cost}" if _big_buy_name != "—" else "—"
            _all_time_goals = get_total_goals(selected_manager, data["selections"], data["goals"])
            f1, f2, f3, f4 = st.columns(4)
            f1.metric("Most Selected Player", _loyal_label)
            f2.metric("Biggest Scoring Gameweek", _best_gw_display)
            f3.metric("Most Expensive Buy", _big_buy_display)
            f4.metric("All-Time Goals", _all_time_goals)

            _best_name, _best_season, _best_pts = get_best_player_season(
                selected_manager, data["selections"], data["goals"], players_df, seasons_df
            )
            _top_label = f"{_best_name} - {_best_pts}pts ({_best_season})" if _best_name != "—" else "—"
            _fav_club, _fav_count = get_favourite_club(selected_manager, data["selections"], players_df)
            _fav_label = f"{_fav_club} - {_fav_count} Players selected" if _fav_club != "—" else "—"
            bp1, bp2 = st.columns(2)
            bp1.metric("Most Selected Club", _fav_label)
            bp2.metric("Top Scoring Player", _top_label)

            # ── Year-by-year position table ───────────────────────────────
            pos_colours = {1: "#2563eb", 2: "#15803d", 3: "#86efac"}
            # Fill in seasons the manager didn't participate in
            _participated = set(history_df["Season"].tolist())
            _min_szn = min(_participated)
            _max_szn = max(_participated)
            all_szns = [s for s in season_options if _min_szn <= s <= _max_szn]
            _hy_full = []
            for szn in all_szns:
                row = history_df[history_df["Season"] == szn]
                if not row.empty:
                    r = row.iloc[0]
                    _hy_full.append({"Season": szn, "Position": int(r["Position"]),
                                     "Points": int(r["Points"]), "Prize": r["Prize"], "played": True})
                else:
                    _hy_full.append({"Season": szn, "Position": None, "Points": None,
                                     "Prize": None, "played": False})
            _hy_sorted = pd.DataFrame(_hy_full).reset_index(drop=True)
            _top_per_season = get_top_player_per_season(
                selected_manager, data["selections"], data["goals"], players_df, seasons_df
            )
            _code_to_fpl = players_df.set_index("code")["_fpl_code"].to_dict()
            def _arrow(d: int, good_up: bool) -> str:
                if d == 0:
                    return '<span style="color:#6b7280;font-size:0.8em"> —</span>'
                up = d > 0 if good_up else d < 0
                colour = "#22c55e" if up else "#ef4444"
                sym = "↑" if up else "↓"
                return f'<span style="color:{colour};font-size:0.8em"> {sym}{abs(d)}</span>'

            hy_rows = []
            for _hy_i, (_, r) in enumerate(_hy_sorted.iterrows()):
                stripe = "background:rgba(128,128,128,0.06);" if _hy_i % 2 == 1 else ""
                if not r["played"]:
                    hy_rows.append(
                        f'<tr style="{stripe}">'
                        f'<td style="padding:6px 14px;color:#6b7280">{r["Season"]}</td>'
                        f'<td style="padding:6px 14px;text-align:center;color:#6b7280">—</td>'
                        f'<td style="padding:6px 14px;text-align:right;color:#6b7280">—</td>'
                        f'<td style="padding:6px 14px;text-align:right;color:#6b7280">—</td>'
                        f'<td style="padding:6px 14px;text-align:right;color:#6b7280">—</td>'
                        f'</tr>'
                    )
                    continue

                pos = int(r["Position"])
                pts = int(r["Points"])
                prize = r["Prize"]
                pc = pos_colours.get(pos, "")
                pos_style = f"color:{pc};font-weight:700;" if pc else "font-weight:500;"
                prize_str = f"£{prize:.0f}" if prize else "—"
                _tp = _top_per_season.get(r["Season"])
                if _tp and _tp[1] > 0:
                    _tp_name, _tp_pts, _tp_code = _tp
                    _tp_fpl = _code_to_fpl.get(_tp_code, "")
                    if _tp_fpl:
                        _tp_href = (
                            f"?player={urllib.parse.quote(_tp_fpl)}"
                            f"&mgr={urllib.parse.quote(selected_manager)}"
                            f"&mgr_season={urllib.parse.quote(r['Season'])}"
                            f"&table_season={urllib.parse.quote(season_id)}"
                        )
                        top_player_str = f'<a href="{_tp_href}" target="_self" style="color:#60a5fa;text-decoration:none">{_tp_name} ({_tp_pts}pts)</a>'
                    else:
                        top_player_str = f"{_tp_name} ({_tp_pts}pts)"
                else:
                    top_player_str = "—"

                # Find the most recent older season they actually played for delta
                prev_played = _hy_sorted[((_hy_sorted.index > _hy_i) & (_hy_sorted["played"] == True))]
                if not prev_played.empty:
                    pr = prev_played.iloc[0]
                    pos_delta = _arrow(int(pr["Position"]) - pos, good_up=True)
                    pts_delta = _arrow(pts - int(pr["Points"]), good_up=True)
                else:
                    pos_delta = pts_delta = ""

                hy_rows.append(
                    f'<tr style="{stripe}">'
                    f'<td style="padding:6px 14px">{r["Season"]}</td>'
                    f'<td style="padding:6px 14px;text-align:center;{pos_style}">{_ordinal(pos)}{pos_delta}</td>'
                    f'<td style="padding:6px 14px;text-align:right">{pts}{pts_delta}</td>'
                    f'<td style="padding:6px 14px;text-align:right">{top_player_str}</td>'
                    f'<td style="padding:6px 14px;text-align:right">{prize_str}</td>'
                    f'</tr>'
                )
            hy_html = (
                '<style>'
                'table.hy{width:100%;border-collapse:collapse;font-size:0.92em}'
                'table.hy th{padding:6px 14px;text-align:left;border-bottom:2px solid #e5e7eb;'
                'color:#6b7280;font-weight:600;font-size:0.75em;text-transform:uppercase;letter-spacing:0.04em}'
                'table.hy th:nth-child(2),table.hy th:nth-child(3),table.hy th:nth-child(4){text-align:right}'
                'table.hy th:nth-child(2){text-align:center}'
                'table.hy tr:hover td{background:rgba(128,128,128,0.1)!important}'
                '</style>'
                '<table class="hy"><thead><tr>'
                '<th>Season</th><th>Position</th><th>Points</th><th style="text-align:right">Top Player</th><th>Prize</th>'
                f'</tr></thead><tbody>{"".join(hy_rows)}</tbody></table>'
            )
            st.markdown(hy_html, unsafe_allow_html=True)

            import altair as alt
            st.markdown("**Position by season**")
            _pos_df = history_df[["Season", "Position"]].copy()
            _pos_chart = (
                alt.Chart(_pos_df)
                .mark_line(point=True, color="#22c55e")
                .encode(
                    x=alt.X("Season:O", axis=alt.Axis(labelAngle=0)),
                    y=alt.Y("Position:Q", scale=alt.Scale(domain=[0, 20], reverse=True), axis=alt.Axis(tickMinStep=1)),
                )
                .properties(height=200)
            )
            st.altair_chart(_pos_chart, use_container_width=True)


# ══ Tab 3: Players ══════════════════════════════════════════════════════════════

with tab_players:
    current_season_id_p = season_options[0]
    season_row_p = seasons_df[seasons_df["season_id"] == current_season_id_p].iloc[0]
    last_gw_p = int(season_row_p.get("last_gw_synced", 0) or 0)

    current_players = players_df[
        (players_df["season"] == current_season_id_p) & (players_df["type"] == "player")
    ]

    # Build a deduplicated player list across all seasons (GKP excluded — they're team slots).
    all_players = players_df[
        (players_df["type"] == "player") & (players_df["fpl_position"] != "GKP")
    ].copy()

    if all_players.empty:
        st.info("No player data available. Run sync-scores to populate.")
    else:
        # Team name lookup per season: (season, element_id) -> team_name
        teams_df_all = players_df[players_df["type"] == "team"]
        team_lookup: dict[tuple, str] = {
            (r["season"], str(r["element_id"])): r["friendly_name"]
            for _, r in teams_df_all.iterrows()
        }

        # Use the stable FPL photo code (embedded in photo_url) as player identity.
        # friendly_name (web_name) changes between seasons; full_name can collide across
        # different players (e.g. two "Ben Davies"). The photo code is truly unique per person.
        # _fpl_code column is already computed on players_df at load time.
        all_players_sorted = all_players.sort_values("season", ascending=False)

        seen_fpl_codes: set[str] = set()
        labelled = []
        for _, r in all_players_sorted.iterrows():
            fname = r.get("friendly_name", "")
            fpl_c = r["_fpl_code"]
            if not fname or not fpl_c or fpl_c in seen_fpl_codes:
                continue
            seen_fpl_codes.add(fpl_c)
            team = team_lookup.get((r["season"], str(r.get("team_id", ""))), "")
            label = f"{fname} - {team}" if team else fname
            labelled.append({"label": label, "friendly_name": fname, "fpl_code": fpl_c, "team": team})
        labelled.sort(key=lambda x: (x["team"], x["friendly_name"]))

        # Resolve ?player=<fpl_code> navigation from squad table links
        if "_pending_player_fpl_code" in st.session_state:
            _pcode = st.session_state["_pending_player_fpl_code"]
            _pmatch = next((p for p in labelled if p["fpl_code"] == _pcode), None)
            if _pmatch:
                st.session_state["_pl_selected_fpl_code"] = _pcode
                st.session_state["_pl_search_gen"] = st.session_state.get("_pl_search_gen", 0) + 1
            if not _is_autonav_pl:
                del st.session_state["_pending_player_fpl_code"]

        _pl_sel_code = st.session_state.get("_pl_selected_fpl_code")
        _selected = next((p for p in labelled if p["fpl_code"] == _pl_sel_code), None) if _pl_sel_code else None
        _pl_search_gen = st.session_state.get("_pl_search_gen", 0)

        if _selected:
            _pc1, _pc2 = st.columns([5, 1])
            _pc1.markdown(f"**{_selected['label']}**")
            if _pc2.button("✕ Clear", key="pl_clear_sel"):
                del st.session_state["_pl_selected_fpl_code"]
                st.session_state["_pl_search_gen"] = _pl_search_gen + 1
                st.rerun()
        else:
            _pl_search = st.text_input(
                "", placeholder="Search by name or team...",
                key=f"pl_search_{_pl_search_gen}", label_visibility="collapsed",
            )
            if _pl_search:
                _pl_matches = [p for p in labelled if _pl_search.lower() in p["label"].lower()][:8]
                if len(_pl_matches) == 1:
                    st.session_state["_pl_selected_fpl_code"] = _pl_matches[0]["fpl_code"]
                    st.session_state["_pl_search_gen"] = _pl_search_gen + 1
                    st.rerun()
                elif _pl_matches:
                    for _pm in _pl_matches:
                        if st.button(_pm["label"], key=f"plr_{_pm['fpl_code']}", use_container_width=True):
                            st.session_state["_pl_selected_fpl_code"] = _pm["fpl_code"]
                            st.session_state["_pl_search_gen"] = _pl_search_gen + 1
                            st.rerun()
                else:
                    st.caption("No players found.")

        selected_friendly = _selected["friendly_name"] if _selected else None
        selected_fpl_code = _selected["fpl_code"] if _selected else None

        if selected_friendly:
            # Use stable FPL photo code to find all seasons for this player.
            if selected_fpl_code:
                p_rows = all_players_sorted[all_players_sorted["_fpl_code"] == selected_fpl_code]
            else:
                p_rows = all_players_sorted[all_players_sorted["friendly_name"] == selected_friendly]
            if p_rows.empty:
                st.warning("Player not found.")
            else:
                # Use current season entry if available, else most recent
                curr_rows = p_rows[p_rows["season"] == current_season_id_p]
                p = curr_rows.iloc[0] if not curr_rows.empty else p_rows.iloc[0]
                p_season_id_p = p["season"]

                # ── Profile card ──────────────────────────────────────────────
                owner_rows = data["selections"][
                    (data["selections"]["player_code"] == p["code"]) &
                    (data["selections"]["season_id"] == p_season_id_p)
                ] if not data["selections"].empty else pd.DataFrame()

                with st.container(border=True):
                    col1, col2 = st.columns([1, 4])
                    with col1:
                        _PLACEHOLDER = "https://resources.premierleague.com/premierleague25/photos/players/110x140/placeholder.png"
                        photo = p.get("photo_url") or ""
                        display_photo = photo if photo and _photo_exists(photo) else _PLACEHOLDER
                        st.markdown(
                            f'<div style="text-align:center;padding-top:18px">'
                            f'<img src="{display_photo}" width="110" style="border-radius:4px">'
                            f'</div>',
                            unsafe_allow_html=True,
                        )
                    with col2:
                        st.subheader(p["friendly_name"])
                        team_row = players_df[
                            (players_df["season"] == p_season_id_p) &
                            (players_df["type"] == "team") &
                            (players_df["element_id"].astype(str) == str(p.get("team_id", "")))
                        ]
                        team_name = team_row.iloc[0]["friendly_name"] if not team_row.empty else ""
                        caption_parts = []
                        if p.get("full_name"):
                            caption_parts.append(p["full_name"])
                        if team_name:
                            caption_parts.append(team_name)
                        st.caption(" · ".join(caption_parts))

                        if p.get("fpl_position") not in ("GKP", ""):
                            _all_time_goals = 0
                            _curr_season_goals: int | None = None
                            _cost_totals_stat: list[float] = []
                            for _ssid in p_rows["season"].unique():
                                _all_codes_s = p_rows[p_rows["season"] == _ssid]["code"].tolist()
                                _g_stat = data["goals"][
                                    data["goals"]["player_code"].isin(_all_codes_s) &
                                    (data["goals"]["season_id"] == _ssid)
                                ] if not data["goals"].empty else pd.DataFrame()
                                _g_pgw_stat = _g_stat[_g_stat["game_week"].astype(str) != "0"] if not _g_stat.empty else _g_stat
                                _season_goals = int((_g_pgw_stat if not _g_pgw_stat.empty else _g_stat)["goals_scored"].astype(int).sum()) if not _g_stat.empty else 0
                                _all_time_goals += _season_goals
                                if _ssid == current_season_id_p:
                                    _curr_season_goals = _season_goals
                                if not data["selections"].empty:
                                    _sel_stat = data["selections"][
                                        data["selections"]["player_code"].isin(_all_codes_s) &
                                        (data["selections"]["season_id"] == _ssid)
                                    ]
                                    if not _sel_stat.empty:
                                        _c = _sel_stat.iloc[0].get("cost", "")
                                        if _c and str(_c) not in ("", "nan"):
                                            try:
                                                _cost_totals_stat.append(float(_c))
                                            except ValueError:
                                                pass
                            _n_seasons = len(p_rows["season"].unique())
                            _avg_per_season = _all_time_goals / _n_seasons if _n_seasons else 0
                            _goals_line = f"⚽ {_all_time_goals} all-time goals (avg {_avg_per_season:.1f} per season)"
                            if _curr_season_goals is not None:
                                _goals_line += f"  ·  {_curr_season_goals} this season"
                            st.caption(_goals_line)
                            if _cost_totals_stat:
                                _avg_c = sum(_cost_totals_stat) / len(_cost_totals_stat)
                                st.caption(f"£{_avg_c:.0f} avg cost")

                        _is_current_season = p_season_id_p == current_season_id_p
                        if not _is_current_season:
                            st.caption(f"Not in the current PL squad — last seen in {p_season_id_p}")

                        if p.get("news"):
                            st.warning(p["news"])

                    # Only show season-specific stats if this is the current season.

                    season_goals_rows = data["goals"][
                        (data["goals"]["player_code"] == p["code"]) &
                        (data["goals"]["season_id"] == p_season_id_p)
                    ] if not data["goals"].empty and _is_current_season else pd.DataFrame()
                    _has_per_gw = not season_goals_rows.empty and (season_goals_rows["game_week"].astype(str) != "0").any()
                    _sgr_stat = season_goals_rows[season_goals_rows["game_week"].astype(str) != "0"] if _has_per_gw else season_goals_rows

                    if not _is_current_season:
                        oc0, oc1, oc2, oc3, oc4, oc5 = st.columns(6)
                        oc0.metric("Manager", "—")
                        oc1.metric("Position", "—")
                        oc2.metric("Cost", "—")
                        oc3.metric("Gameweeks", "—")
                        oc4.metric("Goals", "—")
                        oc5.metric("Points", "—")
                    elif not owner_rows.empty:
                        owner = owner_rows.iloc[0]["manager_name"]
                        cost = owner_rows.iloc[0].get("cost", "")
                        sel_pos = owner_rows.iloc[0].get("position", "")
                        is_gk = sel_pos.upper() == "GK" if sel_pos else False
                        stat_label = "Goals Conceded" if is_gk else "Goals"
                        stat_col = "goals_conceded" if is_gk else "goals_scored"
                        stat_val = int(_sgr_stat[stat_col].astype(int).sum()) if not _sgr_stat.empty else 0

                        season_pts = None
                        s_row = seasons_df[seasons_df["season_id"] == p_season_id_p]
                        up_to = int(s_row.iloc[0].get("last_gw_synced", 0) or 0) if not s_row.empty else 0
                        if sel_pos:
                            if _has_per_gw and up_to:
                                gf = _int(owner_rows.iloc[0]["gw_from"])
                                gt_raw = owner_rows.iloc[0].get("gw_to", "")
                                gt = up_to if str(gt_raw) in ("", "nan") else min(_int(gt_raw), up_to)
                                pts_calc = player_gw_points(p["code"], sel_pos, p_season_id_p, up_to, gf, gt, data["goals"], players_df, data.get("overrides"))
                                season_pts = int(pts_calc["Pts"].sum()) if not pts_calc.empty else 0
                            elif not season_goals_rows.empty and sel_pos.upper() not in ("GK",):
                                total_g = int(season_goals_rows["goals_scored"].astype(int).sum())
                                season_pts = int(score_outfield_player(sel_pos.upper(), total_g))

                        gf_p = owner_rows.iloc[0].get("gw_from", "")
                        gt_p = owner_rows.iloc[0].get("gw_to", "")
                        gw_range_p = ""
                        if str(gf_p) not in ("", "nan") and str(gt_p) not in ("", "nan"):
                            gw_range_p = f"GW{int(float(gf_p))}–{int(float(gt_p))}"
                        elif str(gf_p) not in ("", "nan"):
                            gw_range_p = f"GW{int(float(gf_p))}+"

                        oc0, oc1, oc2, oc3, oc4, oc5 = st.columns(6)
                        oc0.metric("Manager", owner)
                        oc1.metric("Position", sel_pos.upper() if sel_pos else "—")
                        oc2.metric("Cost", f"£{float(cost):.0f}" if cost and str(cost) not in ("", "nan") else "—")
                        oc3.metric("Gameweeks", gw_range_p if gw_range_p else "—")
                        oc4.metric(stat_label, stat_val)
                        oc5.metric("Points", season_pts if season_pts is not None else 0)
                    else:
                        # Not owned — show goals, em-dash ownership-specific fields
                        stat_val = int(_sgr_stat["goals_scored"].astype(int).sum()) if not _sgr_stat.empty else 0
                        oc0, oc1, oc2, oc3, oc4, oc5 = st.columns(6)
                        oc0.metric("Manager", "—")
                        oc1.metric("Position", "—")
                        oc2.metric("Cost", "—")
                        oc3.metric("Gameweeks", "—")
                        oc4.metric("Goals", stat_val)
                        oc5.metric("Points", "—")

                # ── Current season GW table ───────────────────────────────────
                st.divider()
                curr_p_rows = p_rows[p_rows["season"] == current_season_id_p]
                st.markdown("**Current Season breakdown**")
                if curr_p_rows.empty:
                    st.info(f"Not registered in the {current_season_id_p} season.")
                else:
                    gw_p_code = curr_p_rows.iloc[0]["code"]
                    gw_season_row = seasons_df[seasons_df["season_id"] == current_season_id_p]
                    last_gw_for_season = int(gw_season_row.iloc[0].get("last_gw_synced", 0) or 0) if not gw_season_row.empty else 0
                    gw_owner_rows = data["selections"][
                        (data["selections"]["player_code"] == gw_p_code) &
                        (data["selections"]["season_id"] == current_season_id_p)
                    ] if not data["selections"].empty else pd.DataFrame()
                    gw_pos = gw_owner_rows.iloc[0].get("position", "") if not gw_owner_rows.empty else ""

                    if not last_gw_for_season:
                        st.info("No gameweek data synced for this season.")
                    else:
                        if gw_owner_rows.empty:
                            # Not owned — use FPL position for points calculation
                            fpl_pos = curr_p_rows.iloc[0].get("fpl_position", "")
                            pos_map = {"GKP": "GK", "DEF": "DEF", "MID": "MID", "FWD": "FWD"}
                            gw_pos = pos_map.get(fpl_pos, "FWD")
                            gw_from, gw_to = 1, last_gw_for_season
                            st.caption("Not owned this season")
                        else:
                            sel_row = gw_owner_rows.iloc[0]
                            gw_from = _int(sel_row["gw_from"])
                            gw_to_raw = sel_row.get("gw_to", "")
                            gw_to = last_gw_for_season if str(gw_to_raw) in ("", "nan") else min(_int(gw_to_raw), last_gw_for_season)

                        pts_df = player_gw_points(
                            gw_p_code, gw_pos, current_season_id_p, last_gw_for_season,
                            gw_from, gw_to, data["goals"], players_df, data.get("overrides"),
                        )

                        if not pts_df.empty:
                            total_pts = int(pts_df["Pts"].sum())
                            goals_col = "Goals Conceded" if "Goals Conceded" in pts_df.columns else "Goals"
                            total_goals = int(pts_df[goals_col].sum())
                            hide_zeros = st.toggle("Hide blank gameweeks", value=True, key="pl_hide_zeros")
                            gw_display = pts_df[pts_df["Pts"] != 0].copy() if hide_zeros else pts_df.copy()

                            gw_rows = []
                            for i, (_, row) in enumerate(gw_display.iterrows()):
                                stripe = "background:rgba(128,128,128,0.06);" if i % 2 == 1 else ""
                                pts_val = int(row["Pts"])
                                goals_val = int(row[goals_col])
                                pts_style = "color:#22c55e;font-weight:bold;" if pts_val > 0 else ""
                                gw_rows.append(
                                    f'<tr style="{stripe}">'
                                    f'<td style="padding:6px 12px">GW{int(row["GW"])}</td>'
                                    f'<td style="padding:6px 12px;text-align:right">{goals_val}</td>'
                                    f'<td style="padding:6px 12px;text-align:right;{pts_style}">{pts_val}</td>'
                                    f'</tr>'
                                )
                            gw_rows.append(
                                f'<tr style="border-top:2px solid #e5e7eb;font-weight:bold">'
                                f'<td style="padding:6px 12px">Total</td>'
                                f'<td style="padding:6px 12px;text-align:right">{total_goals}</td>'
                                f'<td style="padding:6px 12px;text-align:right">{total_pts}</td>'
                                f'</tr>'
                            )
                            gw_html = (
                                '<style>'
                                'table.gw{width:100%;border-collapse:collapse;font-size:0.92em}'
                                'table.gw th{padding:6px 12px;text-align:left;border-bottom:2px solid #e5e7eb;'
                                'color:#6b7280;font-weight:600;font-size:0.75em;text-transform:uppercase;letter-spacing:0.04em}'
                                'table.gw th:nth-child(2),table.gw th:nth-child(3){text-align:right}'
                                'table.gw tr:hover td{background:rgba(128,128,128,0.1)!important}'
                                '</style>'
                                '<table class="gw"><thead><tr>'
                                f'<th>GW</th><th>{goals_col}</th><th>Points</th>'
                                f'</tr></thead><tbody>{"".join(gw_rows)}</tbody></table>'
                            )
                            st.markdown(gw_html, unsafe_allow_html=True)
                        else:
                            st.info("No points recorded yet.")

                # ── Season history (all seasons) ──────────────────────────────
                # Use stable FPL photo code to match the same player across seasons.
                _p_fpl_code = p.get("_fpl_code", "")
                if _p_fpl_code:
                    all_seasons_sorted = all_season_ids
                    hist_rows = []
                    chart_rows = []
                    for sid in all_seasons_sorted:
                        match = players_df[
                            (players_df["season"] == sid) &
                            (players_df["type"] == "player") &
                            (players_df["_fpl_code"] == _p_fpl_code)
                        ]
                        if match.empty:
                            continue
                        # Collect all codes for this player+season — vaastav uses historical IDs,
                        # FPL history uses current IDs; join goals/selections across all of them.
                        all_codes_h = match["code"].tolist()
                        prev_player = match.iloc[0]
                        prev_team = team_lookup.get((sid, str(prev_player.get("team_id", ""))), "—")
                        # Pre-2023-24 vaastav data has placeholder names ("Team 1" etc.) — fall back to current season
                        if not prev_team or prev_team.startswith("Team ") or prev_team == "—":
                            prev_team = team_lookup.get((current_season_id_p, str(prev_player.get("team_id", ""))), prev_team)
                        # Historical alias rows have no team_id — extract team from the player code suffix
                        if (not prev_player.get("team_id")) and "-player-" in str(prev_player.get("code", "")):
                            _code_suffix = str(prev_player["code"]).split("-player-", 1)[-1]
                            if " - " in _code_suffix:
                                prev_team = _code_suffix.rsplit(" - ", 1)[-1].strip()

                        g_rows = data["goals"][
                            data["goals"]["player_code"].isin(all_codes_h) &
                            (data["goals"]["season_id"] == sid)
                        ] if not data["goals"].empty else pd.DataFrame()
                        if g_rows.empty:
                            goals_total = 0
                        else:
                            _g_pgw = g_rows[g_rows["game_week"].astype(str) != "0"]
                            goals_total = int((_g_pgw if not _g_pgw.empty else g_rows)["goals_scored"].astype(int).sum())

                        owner_str, cost_str, pos_str, gw_range_h = "—", "—", "", "—"
                        cost_numeric = None
                        pts_str = "—"
                        if not data["selections"].empty:
                            sel = data["selections"][
                                data["selections"]["player_code"].isin(all_codes_h) &
                                (data["selections"]["season_id"] == sid)
                            ]
                            if not sel.empty:
                                owner_str = sel.iloc[0]["manager_name"]
                                c = sel.iloc[0].get("cost", "")
                                if c and str(c) not in ("", "nan"):
                                    cost_numeric = float(c)
                                    cost_str = f"£{cost_numeric:.0f}"
                                pos_str = str(sel.iloc[0].get("position", "") or "").upper()

                                gw_from_h = str(sel.iloc[0].get("gw_from", "") or "")
                                gw_to_h = str(sel.iloc[0].get("gw_to", "") or "")
                                if gw_from_h not in ("", "nan") and gw_to_h not in ("", "nan"):
                                    gw_range_h = f"GW{int(float(gw_from_h))}–{int(float(gw_to_h))}"
                                elif gw_from_h not in ("", "nan"):
                                    gw_range_h = f"GW{int(float(gw_from_h))}+"

                                sid_row = seasons_df[seasons_df["season_id"] == sid]
                                up_to = int(sid_row.iloc[0].get("last_gw_synced", 0) or 0) if not sid_row.empty else 0

                                _has_per_gw_h = not g_rows.empty and (g_rows["game_week"].astype(str) != "0").any()
                                if _has_per_gw_h and up_to:
                                    gf = _int(sel.iloc[0]["gw_from"])
                                    gt_raw = sel.iloc[0].get("gw_to", "")
                                    gt = up_to if str(gt_raw) in ("", "nan") else min(_int(gt_raw), up_to)
                                    # Use whichever code has per-GW data
                                    _pgw_code = g_rows[g_rows["game_week"].astype(str) != "0"].iloc[0]["player_code"]
                                    prev_pts = player_gw_points(
                                        _pgw_code, sel.iloc[0]["position"], sid, up_to,
                                        gf, gt, data["goals"], players_df, data.get("overrides"),
                                    )
                                    pts_str = str(int(prev_pts["Pts"].sum())) if not prev_pts.empty else "0"
                                elif not g_rows.empty and pos_str not in ("GK", ""):
                                    total_goals_h = int(g_rows["goals_scored"].astype(int).sum())
                                    pts_str = str(int(score_outfield_player(pos_str, total_goals_h)))
                                elif up_to:
                                    pts_str = "0"

                        badge_url = team_badge_lookup.get(prev_team, "")
                        hist_rows.append({
                            "Season": sid,
                            "Badge": badge_url,
                            "Team": prev_team,
                            "Pos": pos_str,
                            "Owner": owner_str,
                            "Cost": cost_str,
                            "Gameweeks": gw_range_h,
                            "Goals_raw": goals_total if not g_rows.empty else None,
                            "Points": pts_str,
                        })

                        if cost_numeric is not None or goals_total:
                            chart_row: dict = {"Season": sid}
                            if goals_total:
                                chart_row["Goals"] = goals_total
                            if cost_numeric is not None:
                                chart_row["Cost (£)"] = cost_numeric
                            chart_rows.append(chart_row)

                    if hist_rows:
                        st.divider()
                        st.markdown("**Season history**")

                        goals_vals = [r["Goals_raw"] for r in hist_rows]
                        pts_vals = []
                        for r in hist_rows:
                            try:
                                pts_vals.append(int(r["Points"]))
                            except (ValueError, TypeError):
                                pts_vals.append(None)

                        def _with_delta(val, vals: list, i: int) -> str:
                            cur = vals[i]
                            if cur is None:
                                return str(val)
                            nxt = vals[i + 1] if i + 1 < len(vals) else None
                            if nxt is None:
                                return str(cur)
                            prev_val = nxt if isinstance(nxt, int) else 0
                            d = cur - prev_val
                            if d > 0:
                                return f"{cur} (↑{d})"
                            if d < 0:
                                return f"{cur} (↓{abs(d)})"
                            return f"{cur} (— 0)"

                        display_rows = []
                        for i, row in enumerate(hist_rows):
                            goals_display = _with_delta(
                                row["Goals_raw"] if row["Goals_raw"] is not None else "—",
                                goals_vals, i,
                            ) if row["Goals_raw"] is not None else "—"
                            pts_display = _with_delta(row["Points"], pts_vals, i)
                            display_rows.append({
                                "Season": row["Season"],
                                "Badge": row["Badge"],
                                "Team": row["Team"],
                                "Pos": row["Pos"],
                                "Owner": row["Owner"],
                                "Cost": row["Cost"],
                                "GWs": row["Gameweeks"],
                                "Goals": goals_display,
                                "Points": pts_display,
                            })

                        def _cell_style(val: str) -> str:
                            if "↑" in str(val):
                                return "color:#22c55e;"
                            if "↓" in str(val):
                                return "color:#ef4444;"
                            return ""

                        sh_rows = []
                        for i, row in enumerate(display_rows):
                            stripe = "background:rgba(128,128,128,0.06);" if i % 2 == 1 else ""
                            badge_url = row.get("Badge", "")
                            badge_html = (
                                f'<img src="{badge_url}" height="16" '
                                f'style="vertical-align:middle;margin-right:5px;border-radius:2px;filter:brightness(0.75)">'
                                if badge_url else ""
                            )
                            sh_rows.append(
                                f'<tr style="{stripe}">'
                                f'<td style="padding:6px 12px">{row["Season"]}</td>'
                                f'<td style="padding:6px 12px">{badge_html}{row["Team"]}</td>'
                                f'<td style="padding:6px 12px">{row["Pos"]}</td>'
                                f'<td style="padding:6px 12px">{row["Owner"]}</td>'
                                f'<td style="padding:6px 12px">{row["Cost"]}</td>'
                                f'<td style="padding:6px 12px">{row["GWs"]}</td>'
                                f'<td style="padding:6px 12px;{_cell_style(row["Goals"])}">{row["Goals"]}</td>'
                                f'<td style="padding:6px 12px;{_cell_style(row["Points"])}">{row["Points"]}</td>'
                                f'</tr>'
                            )
                        sh_html = (
                            '<style>'
                            'table.sh{width:100%;border-collapse:collapse;font-size:0.92em}'
                            'table.sh th{padding:6px 12px;text-align:left;border-bottom:2px solid #e5e7eb;'
                            'color:#6b7280;font-weight:600;font-size:0.75em;text-transform:uppercase;letter-spacing:0.04em}'
                            'table.sh tr:hover td{background:rgba(128,128,128,0.1)!important}'
                            '</style>'
                            '<table class="sh"><thead><tr>'
                            '<th>Season</th><th>Team</th><th>Pos</th><th>Owner</th>'
                            '<th>Cost</th><th>GWs</th><th>Goals</th><th>Points</th>'
                            f'</tr></thead><tbody>{"".join(sh_rows)}</tbody></table>'
                        )
                        st.markdown(sh_html, unsafe_allow_html=True)

                        if chart_rows:
                            chart_df = pd.DataFrame(chart_rows).set_index("Season").sort_index()
                            goal_cols = [c for c in ["Goals"] if c in chart_df.columns]
                            if goal_cols:
                                import altair as alt  # type: ignore[import-untyped]
                                st.divider()
                                st.markdown("**Goals per season**")
                                _gdf = chart_df[goal_cols].reset_index()
                                _bar_w = max(20, min(60, 300 // max(len(_gdf), 1)))
                                _chart = (
                                    alt.Chart(_gdf)
                                    .mark_bar(color="#22c55e", size=_bar_w)
                                    .encode(
                                        x=alt.X("Season:O", axis=alt.Axis(labelAngle=0)),
                                        y=alt.Y("Goals:Q", axis=alt.Axis(tickMinStep=1)),
                                    )
                                    .properties(height=200)
                                )
                                st.altair_chart(_chart, use_container_width=True)

# ══ Tab 4: Trophy Cabinet ══════════════════════════════════════════════════════

with tab_stats:
    _sc = compute_stats_corner(
        data["selections"], data["goals"], data.get("overrides", pd.DataFrame()),
        players_df, seasons_df, standings_df,
    )

    _tc_td = 'padding:9px 14px;border-bottom:1px solid #e5e7eb;vertical-align:middle'
    _tc_th = ('padding:7px 14px;text-align:left;border-bottom:2px solid #e5e7eb;'
              'color:#6b7280;font-weight:600;font-size:0.75em;text-transform:uppercase;letter-spacing:0.04em')
    _tc_style = (
        '<style>'
        'table.tc{width:100%;border-collapse:collapse;font-size:0.92em}'
        'table.tc td{' + _tc_td + '}'
        'table.tc th{' + _tc_th + '}'
        'table.tc tr:hover td{background:rgba(128,128,128,0.07)!important}'
        '</style>'
    )

    def _bold(s: str) -> str:
        return f'<strong>{s}</strong>'

    def _dim(s: str) -> str:
        return f'<span style="color:#6b7280">{s}</span>'

    def _sep() -> str:
        return _dim(" &nbsp;·&nbsp; ")

    def _cost_s(cost: float) -> str:
        return f"£{int(cost)}" if cost else ""

    def _player_val(r: dict | None, detail: str) -> str:
        if not r:
            return "—"
        return _bold(r["player"]) + _sep() + _dim(detail)

    def _tc_row(label: str, value: str) -> str:
        return (
            f'<tr>'
            f'<td style="{_tc_td};white-space:nowrap;width:32%">{label}</td>'
            f'<td style="{_tc_td}">{value}</td>'
            f'</tr>'
        )

    def _r(rec: dict | None) -> dict:
        return rec or {}

    _records: list[tuple[str, str]] = []

    _r1 = _r(_sc.get("most_expensive"))
    _records.append(("Most Expensive Player", _player_val(
        _r1, f'{_cost_s(_r1.get("cost", 0))}{_sep()}{_r1.get("manager","")}{_sep()}{_r1.get("season","")}'
        ) if _r1 else "—"))

    _r2 = _r(_sc.get("most_pts_season"))
    _records.append(("Best Season (Player)", _player_val(
        _r2, f'{_r2.get("pts",0)}pts{_sep()}{_r2.get("manager","")}{_sep()}{_r2.get("season","")}{_sep()}{_cost_s(_r2.get("cost",0))}'
        ) if _r2 else "—"))

    _r3 = _r(_sc.get("best_value"))
    _records.append(("Best Value Player", _player_val(
        _r3, f'{_r3.get("ratio",0)}pts/£{_sep()}{_r3.get("pts",0)}pts from {_cost_s(_r3.get("cost",0))}{_sep()}{_r3.get("manager","")}{_sep()}{_r3.get("season","")}'
        ) if _r3 else "—"))

    _r4 = _r(_sc.get("biggest_flop"))
    _records.append(("Biggest Flop", _player_val(
        _r4, f'{_r4.get("pts",0)}pts from {_cost_s(_r4.get("cost",0))}{_sep()}{_r4.get("manager","")}{_sep()}{_r4.get("season","")}'
        ) if _r4 else "—"))

    _r5 = _r(_sc.get("hat_trick_hero"))
    _records.append(("Hat-trick Hero", (
        _bold(_r5["player"]) + _sep() + _dim(f'{_r5["count"]} hat-trick{"s" if _r5["count"] != 1 else ""}')
        ) if _r5 else "—"))

    _r6 = _r(_sc.get("most_selected_player"))
    _records.append(("Most Selected Player", (
        _bold(_r6["player"]) + _sep() + _dim(f'picked {_r6["count"]} times across all seasons')
        ) if _r6 else "—"))

    _r7 = _r(_sc.get("transfer_regret"))
    _records.append(("Biggest Transfer Regret", (
        _bold(_r7["player"]) + _sep() + _dim(
            f'{_r7["pts"]}pts for {_r7["manager"]} in {_r7["season"]}'
            f' — dropped by {_r7["prev_manager"]}'
        )) if _r7 else "—"))

    _r8 = _r(_sc.get("highest_season_score"))
    _records.append(("Highest Season Score", (
        _bold(_r8["manager"]) + _sep() + _dim(f'{_r8["pts"]}pts in {_r8["season"]}')
        ) if _r8 else "—"))

    _r9 = _r(_sc.get("highest_scoring_gw"))
    _records.append(("Highest Scoring Gameweek", (
        _bold(f'GW{_r9["gw"]} {_r9["season"]}') + _sep() + _dim(f'{_r9["total_pts"]}pts across all squads')
        ) if _r9 else "—"))

    _r10 = _r(_sc.get("biggest_improvement"))
    _records.append(("Biggest Season Improvement", (
        _bold(_r10["manager"]) + _sep() + _dim(
            f'{_r10["from_pos"]}th → {_r10["to_pos"]}{"st" if _r10["to_pos"]==1 else "nd" if _r10["to_pos"]==2 else "rd" if _r10["to_pos"]==3 else "th"}'
            f' in {_r10["season"]} (↑{_r10["improvement"]})'
        )) if _r10 else "—"))

    _records_html = (
        _tc_style +
        '<table class="tc"><thead><tr>'
        '<th>Record</th><th>Holder &amp; Detail</th>'
        '</tr></thead><tbody>'
        + "".join(_tc_row(label, value) for label, value in _records)
        + '</tbody></table>'
    )
    st.markdown(_records_html, unsafe_allow_html=True)

    st.divider()

    def _lb_html(title: str, rows: list, unit: str = "", avg: bool = False) -> str:
        medal = {0: "🥇", 1: "🥈", 2: "🥉"}
        body = ""
        for i, row in enumerate(rows):
            icon = medal.get(i, f"{i + 1}.")
            if avg:
                name, val, n = row
                detail = f"{val} avg &nbsp;<span style='font-size:0.8em;color:#9ca3af'>({n} seasons)</span>"
            else:
                name, val = row
                detail = f"{val} {unit}"
            body += (
                f'<tr>'
                f'<td style="{_tc_td};width:28px;text-align:center;padding-left:8px">{icon}</td>'
                f'<td style="{_tc_td};font-weight:600">{name}</td>'
                f'<td style="{_tc_td};text-align:right;color:#6b7280;white-space:nowrap">{detail}</td>'
                f'</tr>'
            )
        return (
            _tc_style +
            f'<table class="tc"><thead><tr><th colspan="3">{title}</th></tr></thead>'
            f'<tbody>{body}</tbody></table>'
        )

    _lbc1, _lbc2, _lbc3 = st.columns(3)
    with _lbc1:
        _wins = _sc.get("most_wins", [])
        st.markdown(_lb_html("Most Season Wins", _wins, "wins"), unsafe_allow_html=True)
    with _lbc2:
        _prizes = _sc.get("most_prizes", [])
        st.markdown(_lb_html("Most Prize Finishes", _prizes, "prizes"), unsafe_allow_html=True)
    with _lbc3:
        _avg = _sc.get("avg_position", [])
        st.markdown(_lb_html("Best Average Finish", _avg, avg=True), unsafe_allow_html=True)

    _lbc4, _lbc5 = st.columns(2)
    with _lbc4:
        _unlucky = _sc.get("unluckiest", [])
        st.markdown(_lb_html("Unluckiest (Most 4th Places)", _unlucky, "times"), unsafe_allow_html=True)
    with _lbc5:
        _seasons = _sc.get("most_seasons", [])
        st.markdown(_lb_html("Most Seasons Played", _seasons, "seasons"), unsafe_allow_html=True)
