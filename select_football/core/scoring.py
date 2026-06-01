"""Pure scoring functions — no I/O, no pandas, no side effects."""


POSITION_MODIFIERS: dict[str, float] = {
    "DEF": 3.0,
    "MID": 2.0,
    "FWD": 1.0,
}

GK_CONCEDED_MODIFIER = -1.0
GK_GOAL_BONUS = 4.0


def score_outfield_player(
    position: str,
    goals: int,
) -> float:
    """Return points for an outfield player (DEF / MID / FWD) in one game week.

    Args:
        position: "DEF", "MID", or "FWD"
        goals: number of goals to score (after override applied by caller)
    """
    modifier = POSITION_MODIFIERS.get(position.upper(), 1.0)
    return goals * modifier


def score_gk_team(
    goals_conceded: int,
    gk_goals_scored: int = 0,
) -> float:
    """Return points for a GK team slot in one game week.

    Args:
        goals_conceded: goals conceded by the team in the fixture(s) this GW
        gk_goals_scored: goals scored by the goalkeeper player (for the +4 bonus)
    """
    conceded_score = goals_conceded * GK_CONCEDED_MODIFIER
    bonus_score = gk_goals_scored * GK_GOAL_BONUS
    return conceded_score + bonus_score
