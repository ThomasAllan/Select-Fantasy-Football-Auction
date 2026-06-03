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
    "<style>[data-testid='stDataFrame'] [class*='resizer'], .ag-header-cell-resize { display: none !important; }</style>",
    unsafe_allow_html=True,
)


# ── Data loading ───────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def load_data() -> dict:
    settings = get_settings()
    store = CsvStore(settings.data_dir)
    goals_path = Path(settings.data_dir) / "goals.csv"
    last_updated = (
        datetime.fromtimestamp(goals_path.stat().st_mtime).strftime("%d %b %Y, %H:%M")
        if goals_path.exists()
        else None
    )
    return {
        "seasons": store.read("seasons"),
        "selections": store.read("manager_selections"),
        "goals": store.read("goals"),
        "players": store.read_all_players(),
        "prizes": store.read("prizes"),
        "standings": store.read("standings"),
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

        name = player_names.get(code)
        if not name:
            parts = code.split("-player-")
            name = parts[1] if len(parts) > 1 else code

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
        if not team_id:
            continue
        club = team_name_lookup.get((season_id, team_id), "")
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
) -> dict[str, tuple[str, int]]:
    """Returns {season_id: (player_name, pts)} for the top outfield player each season."""
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
    season_bests: dict[str, tuple[str, int]] = {}

    for _, s in mgr_sels.iterrows():
        code = s["player_code"]
        pos = s["position"].upper()
        season_id = s["season_id"]
        gw_from = _int(s["gw_from"])
        gw_to_raw = s.get("gw_to", "")
        last_gw = last_gw_map.get(season_id, 38)
        gw_to = last_gw if str(gw_to_raw) in ("", "nan") or pd.isna(gw_to_raw) else min(_int(gw_to_raw), last_gw)

        name = player_names.get(code)
        if not name:
            parts = code.split("-player-")
            name = parts[1] if len(parts) > 1 else code

        g_rows = goals_df[
            (goals_df["player_code"] == code) &
            (goals_df["season_id"] == season_id) &
            (goals_df["game_week"].astype(int) >= gw_from) &
            (goals_df["game_week"].astype(int) <= gw_to)
        ]
        total_goals = int(g_rows["goals_scored"].astype(int).sum()) if not g_rows.empty else 0
        pts = int(score_outfield_player(pos, total_goals))
        cur = season_bests.get(season_id, ("—", 0))
        if pts > cur[1]:
            season_bests[season_id] = (name, pts)

    return season_bests


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
) -> pd.DataFrame:
    pos = position.upper()
    rows = []
    for gw in range(gw_from, min(gw_to, up_to_gw) + 1):
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
        gkp_by_team.setdefault(int(r["team_id"]), []).append(r["code"])

    rows = []
    for _, s in sel.iterrows():
        code = s["player_code"]
        pos = s["position"].upper()
        gw_from = _int(s["gw_from"])
        gw_to_raw = s.get("gw_to", "")
        gw_to = up_to_gw if (str(gw_to_raw) == "" or pd.isna(gw_to_raw)) else min(_int(gw_to_raw), up_to_gw)
        name = player_names.get(code, code)

        for gw in range(gw_from, gw_to + 1):
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

tab_table, tab_managers, tab_players = st.tabs(["League Table", "Managers", "Players"])

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
                team_id = str(details.get("team_id", ""))
                team_name = team_names_map.get(team_id, "—") if pos != "GK" else player_names_map.get(code, code)
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
                    "Player": player_names_map.get(code, code),
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
                status_cell_style = f"{td}color:#fca5a5;background:#7f1d1d;" if status_text else td
                cells.append(f'<td style="{status_cell_style}">{status_text}</td>')
                sq_rows_html.append(f'<tr style="{row_style}">{"".join(cells)}</tr>')
            goals_th = '<th style="text-align:right">Goals</th>' if has_goals else ''
            pts_th = '<th style="text-align:right">Points</th>' if has_pts else ''
            sq_html = (
                '<style>'
                'table.sq{width:100%;border-collapse:collapse;font-size:0.92em}'
                'table.sq th{padding:6px 12px;text-align:left;border-bottom:2px solid #e5e7eb;'
                'color:#6b7280;font-weight:600;font-size:0.75em;text-transform:uppercase;letter-spacing:0.04em}'
                'table.sq tr:hover td{background:rgba(128,128,128,0.1)!important}'
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
            h3.metric("Avg Position", _ordinal(round(history_df['Position'].mean())))
            h4.metric("Total Winnings", f"£{history_df['Prize'].sum():.0f}")

            _best_name, _best_season, _best_pts = get_best_player_season(
                selected_manager, data["selections"], data["goals"], players_df, seasons_df
            )
            _top_label = f"{_best_name} - {_best_pts}pts ({_best_season})" if _best_name != "—" else "—"
            _fav_club, _fav_count = get_favourite_club(selected_manager, data["selections"], players_df)
            _fav_label = f"{_fav_club} ({_fav_count})" if _fav_club != "—" else "—"
            bp1, bp2 = st.columns(2)
            bp1.metric("Top All Time Player", _top_label)
            bp2.metric("Favourite Club", _fav_label)

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
                        f'<td style="padding:6px 14px;color:#6b7280">—</td>'
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
                top_player_str = f"{_tp[0]} ({_tp[1]}pts)" if _tp and _tp[1] > 0 else "—"

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
                    f'<td style="padding:6px 14px;color:#9ca3af">{top_player_str}</td>'
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
                '<th>Season</th><th>Position</th><th>Points</th><th>Top Player</th><th>Prize</th>'
                f'</tr></thead><tbody>{"".join(hy_rows)}</tbody></table>'
            )
            st.markdown(hy_html, unsafe_allow_html=True)

            import altair as alt
            st.markdown("**Position by season**")
            _pos_df = history_df[["Season", "Position"]].copy()
            _n_managers = standings_df[standings_df["season_id"].isin(_pos_df["Season"])].groupby("season_id")["manager_name"].count().max() if not standings_df.empty else 16
            _pos_chart = (
                alt.Chart(_pos_df)
                .mark_line(point=True, color="#22c55e")
                .encode(
                    x=alt.X("Season:O", axis=alt.Axis(labelAngle=0)),
                    y=alt.Y("Position:Q", scale=alt.Scale(domain=[1, int(_n_managers or 16)], reverse=True), axis=alt.Axis(tickMinStep=1)),
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
                                pts_calc = player_gw_points(p["code"], sel_pos, p_season_id_p, up_to, gf, gt, data["goals"], players_df)
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
                            gw_from, gw_to, data["goals"], players_df,
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
                                        gf, gt, data["goals"], players_df,
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
