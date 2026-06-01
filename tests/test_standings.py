import pandas as pd
import pytest

from select_football.common.models import Prize
from select_football.core.standings import compute_standings


def _managers():
    return pd.DataFrame([
        {"name": "Alice", "email": "alice@example.com"},
        {"name": "Bob", "email": "bob@example.com"},
    ])


def _selections(season="2025-26"):
    return pd.DataFrame([
        # Alice: 1 DEF, 1 GK team (Arsenal = team 1)
        {"player_code": f"{season}-player-10", "season_id": season, "manager_name": "Alice",
         "player_name": "Defender Dan", "position": "DEF", "cost": "10", "gw_from": "1", "gw_to": "", "team": "Arsenal"},
        {"player_code": f"{season}-team-1", "season_id": season, "manager_name": "Alice",
         "player_name": "Arsenal", "position": "GK", "cost": "5", "gw_from": "1", "gw_to": "", "team": "Arsenal"},
        # Bob: 1 FWD
        {"player_code": f"{season}-player-20", "season_id": season, "manager_name": "Bob",
         "player_name": "Forward Fred", "position": "FWD", "cost": "8", "gw_from": "1", "gw_to": "", "team": "Chelsea"},
    ])


def _goals(season="2025-26"):
    return pd.DataFrame([
        # Alice's DEF scored 1 goal in GW1 → 3 pts
        {"player_code": f"{season}-player-10", "season_id": season, "game_week": "1",
         "goals_scored": "1", "goals_conceded": "0"},
        # Arsenal conceded 2 goals in GW1 → -2 pts
        {"player_code": f"{season}-team-1", "season_id": season, "game_week": "1",
         "goals_scored": "0", "goals_conceded": "2"},
        # Bob's FWD scored 2 goals in GW1 → 2 pts
        {"player_code": f"{season}-player-20", "season_id": season, "game_week": "1",
         "goals_scored": "2", "goals_conceded": "0"},
    ])


class TestStandings:
    def test_basic_standings(self):
        standings = compute_standings(
            season_id="2025-26",
            up_to_gw=1,
            managers_df=_managers(),
            selections_df=_selections(),
            goals_df=_goals(),
            overrides_df=pd.DataFrame(),
            players_df=pd.DataFrame(),
            prizes=[],
        )
        assert len(standings) == 2
        # Alice: DEF 1 goal = 3pts, Arsenal GK 2 conceded = -2pts → net 1pt
        alice = next(s for s in standings if s.manager_name == "Alice")
        assert alice.total_points == 1.0

        # Bob: FWD 2 goals = 2pts
        bob = next(s for s in standings if s.manager_name == "Bob")
        assert bob.total_points == 2.0

        # Bob should be ranked 1st
        assert bob.position == 1
        assert alice.position == 2

    def test_override_sets_final_points(self):
        overrides = pd.DataFrame([
            {"player_code": "2025-26-player-20", "season_id": "2025-26",
             "game_week": "1", "override_points": "1", "reason": "1 goal allowed, 1 rescinded"},
        ])
        standings = compute_standings(
            season_id="2025-26",
            up_to_gw=1,
            managers_df=_managers(),
            selections_df=_selections(),
            goals_df=_goals(),
            overrides_df=overrides,
            players_df=pd.DataFrame(),
            prizes=[],
        )
        bob = next(s for s in standings if s.manager_name == "Bob")
        # Bob's FWD scored 2 goals (2 pts) but override sets final points to 1
        assert bob.total_points == 1.0

    def test_prizes_assigned(self):
        prizes = [Prize(season_id="2025-26", position=1, prize_amount=150.0)]
        standings = compute_standings(
            season_id="2025-26",
            up_to_gw=1,
            managers_df=_managers(),
            selections_df=_selections(),
            goals_df=_goals(),
            overrides_df=pd.DataFrame(),
            players_df=pd.DataFrame(),
            prizes=prizes,
        )
        first = standings[0]
        assert first.prize == 150.0
        second = standings[1]
        assert second.prize is None
