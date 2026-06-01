import pytest

from select_football.core.scoring import score_gk_team, score_outfield_player


class TestOutfieldScoring:
    def test_def_goal(self):
        assert score_outfield_player("DEF", 1) == 3.0

    def test_mid_goal(self):
        assert score_outfield_player("MID", 1) == 2.0

    def test_fwd_goal(self):
        assert score_outfield_player("FWD", 1) == 1.0

    def test_multiple_goals(self):
        assert score_outfield_player("DEF", 2) == 6.0

    def test_zero_goals(self):
        assert score_outfield_player("FWD", 0) == 0.0

    def test_case_insensitive(self):
        assert score_outfield_player("def", 1) == 3.0


class TestGkScoring:
    def test_clean_sheet(self):
        assert score_gk_team(goals_conceded=0) == 0.0

    def test_one_conceded(self):
        assert score_gk_team(goals_conceded=1) == -1.0

    def test_three_conceded(self):
        assert score_gk_team(goals_conceded=3) == -3.0

    def test_gk_scored(self):
        assert score_gk_team(goals_conceded=0, gk_goals_scored=1) == 4.0

    def test_gk_scored_and_conceded(self):
        # GK scores 1 (+4) but concedes 2 (-2) → net +2
        assert score_gk_team(goals_conceded=2, gk_goals_scored=1) == 2.0

    def test_heavy_defeat(self):
        assert score_gk_team(goals_conceded=5) == -5.0
