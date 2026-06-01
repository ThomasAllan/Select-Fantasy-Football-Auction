"""Computes league standings from CSV DataFrames — no file I/O."""
import pandas as pd

from select_football.common.models import ManagerStanding, Prize
from select_football.core.scoring import score_gk_team, score_outfield_player


def _int(val: object, default: int = 0) -> int:
    try:
        return int(val)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return default


def _goals_for(
    goals_df: pd.DataFrame,
    player_code: str,
    season_id: str,
    game_week: int,
    column: str,
) -> int:
    if goals_df.empty or "player_code" not in goals_df.columns:
        return 0
    mask = (
        (goals_df["player_code"] == player_code)
        & (goals_df["season_id"] == season_id)
        & (goals_df["game_week"].astype(str) == str(game_week))
    )
    rows = goals_df[mask]
    if rows.empty:
        return 0
    return int(rows[column].astype(int).sum())


def _override_points(
    overrides_df: pd.DataFrame,
    player_code: str,
    season_id: str,
    game_week: int,
) -> float | None:
    """Return override points or None if no override exists for this player/GW."""
    if overrides_df.empty or "player_code" not in overrides_df.columns:
        return None
    mask = (
        (overrides_df["player_code"] == player_code)
        & (overrides_df["season_id"] == season_id)
        & (overrides_df["game_week"].astype(str) == str(game_week))
    )
    rows = overrides_df[mask]
    if rows.empty:
        return None
    try:
        return float(rows.iloc[0]["override_points"])
    except (ValueError, TypeError):
        return None


def _gk_player_codes_for_team(
    players_df: pd.DataFrame,
    team_id: int,
    season_id: str,
) -> list[str]:
    """Return player codes for all GK players on a given team."""
    required = {"type", "fpl_position", "team_id", "season", "code"}
    if players_df.empty or not required.issubset(players_df.columns):
        return []
    mask = (
        (players_df["type"] == "player")
        & (players_df["fpl_position"] == "GKP")
        & (players_df["team_id"].astype(str) == str(team_id))
        & (players_df["season"] == season_id)
    )
    return players_df[mask]["code"].tolist()


def compute_standings(
    season_id: str,
    up_to_gw: int,
    managers_df: pd.DataFrame,
    selections_df: pd.DataFrame,
    goals_df: pd.DataFrame,
    overrides_df: pd.DataFrame,
    players_df: pd.DataFrame,
    prizes: list[Prize],
) -> list[ManagerStanding]:
    """Compute the full league table for a season up to (and including) up_to_gw.

    managers_df uses 'name' as the primary key (no manager_id).
    selections_df references managers by 'manager_name'.
    All DataFrames have string dtypes (as returned by CsvStore.read).
    Returns standings sorted descending by total_points.
    """
    if managers_df.empty or "name" not in managers_df.columns:
        return []

    manager_names = managers_df["name"].tolist()
    prize_map = {p.position: p.prize_amount for p in prizes}

    totals: dict[str, float] = {name: 0.0 for name in manager_names}
    breakdowns: dict[str, dict[int, float]] = {name: {} for name in manager_names}

    for gw in range(1, up_to_gw + 1):
        for manager_name in manager_names:
            gw_score = 0.0

            # Active selections for this manager in this GW
            active = selections_df[
                (selections_df["season_id"] == season_id)
                & (selections_df["manager_name"] == manager_name)
                & (selections_df["gw_from"].astype(str).apply(lambda x: _int(x)) <= gw)
                & (
                    selections_df["gw_to"].apply(
                        lambda x: True if x == "" or pd.isna(x) else _int(x) >= gw
                    )
                )
            ]

            for _, sel in active.iterrows():
                position = sel["position"].upper()
                player_code = sel["player_code"]

                if position == "GK":
                    # Team code embedded in player_code: "{season}-team-{teamId}"
                    parts = player_code.split("-")
                    team_id = _int(parts[-1]) if parts else 0

                    # Goals conceded via fixture data
                    conceded = _goals_for(goals_df, player_code, season_id, gw, "goals_conceded")

                    # Override sets the final GK points directly
                    ov = _override_points(overrides_df, player_code, season_id, gw)
                    if ov is not None:
                        gw_score += ov
                        continue

                    # GK goal bonus — find individual GK player(s) for this team
                    gk_codes = _gk_player_codes_for_team(players_df, team_id, season_id)
                    gk_goals = sum(
                        _goals_for(goals_df, code, season_id, gw, "goals_scored")
                        for code in gk_codes
                    )
                    gw_score += score_gk_team(conceded, gk_goals)

                else:
                    # Override sets the final points directly, bypassing the position multiplier
                    ov = _override_points(overrides_df, player_code, season_id, gw)
                    if ov is not None:
                        gw_score += ov
                    else:
                        goals = _goals_for(goals_df, player_code, season_id, gw, "goals_scored")
                        gw_score += score_outfield_player(position, goals)

            totals[manager_name] += gw_score
            breakdowns[manager_name][gw] = gw_score

    # Build sorted standings
    standings: list[ManagerStanding] = []
    for rank, (manager_name, total) in enumerate(
        sorted(totals.items(), key=lambda x: x[1], reverse=True), start=1
    ):
        standings.append(
            ManagerStanding(
                position=rank,
                manager_name=manager_name,
                total_points=total,
                prize=prize_map.get(rank),
                gw_breakdown=breakdowns[manager_name],
            )
        )

    return standings
