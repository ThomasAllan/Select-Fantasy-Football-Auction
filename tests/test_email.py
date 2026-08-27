"""Movement / snapshot logic for the monthly report email."""
from select_football.common.models import ManagerStanding
from select_football.email.renderer import render_report
from select_football.jobs.send_report import _compute_movement, _movement_baseline


def _standing(pos: int, name: str) -> ManagerStanding:
    return ManagerStanding(position=pos, manager_name=name, total_points=float(20 - pos))


def test_compute_movement_up_down_same_and_new():
    previous = [
        {"position": 1, "manager_name": "Kev"},
        {"position": 2, "manager_name": "Tom"},
        {"position": 3, "manager_name": "Rory"},
    ]
    standings = [_standing(1, "Rory"), _standing(2, "Kev"), _standing(3, "Sam")]

    movement = _compute_movement(standings, previous)

    assert movement["Rory"] == 2      # 3 -> 1, up two places
    assert movement["Kev"] == -1      # 1 -> 2, down one
    assert movement["Sam"] is None    # not in the previous snapshot


def test_movement_baseline_picks_latest_past_month(monkeypatch):
    import select_football.jobs.send_report as sr

    monkeypatch.setattr(sr, "_current_month", lambda: "2026-09")
    config = {
        "email_standings_history": {
            "2026-07": [{"position": 1, "manager_name": "Jul"}],
            "2026-08": [{"position": 1, "manager_name": "Aug"}],
            "2026-09": [{"position": 1, "manager_name": "Sep"}],  # current month, ignored
        }
    }
    assert _movement_baseline(config) == [{"position": 1, "manager_name": "Aug"}]


def test_movement_baseline_none_when_no_past_month(monkeypatch):
    import select_football.jobs.send_report as sr

    monkeypatch.setattr(sr, "_current_month", lambda: "2026-08")
    assert _movement_baseline({}) is None
    assert _movement_baseline({"email_standings_history": {"2026-08": []}}) is None


def test_render_report_omits_movement_column_when_none():
    html = render_report([_standing(1, "Rory")], season_id="2026-27", gameweek=3)
    assert "After Gameweek 3" in html
    assert "new" not in html and "&#9650;" not in html


def test_render_report_shows_movement_markers():
    html = render_report(
        [_standing(1, "Rory"), _standing(2, "Sam")],
        season_id="2026-27",
        gameweek=3,
        movement={"Rory": 2, "Sam": None},
    )
    assert "&#9650;" in html   # up arrow for Rory
    assert "new" in html       # Sam has no prior snapshot
