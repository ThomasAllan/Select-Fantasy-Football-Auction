"""Movement / snapshot logic for the monthly report email."""
from select_football.common.models import ManagerStanding
from select_football.email.renderer import render_report
from select_football.jobs.send_report import (
    _compute_movement,
    _movement_baseline,
    _parse_manager_emails,
)


def _standing(pos: int, name: str, prize: float | None = None) -> ManagerStanding:
    return ManagerStanding(
        position=pos, manager_name=name, total_points=float(20 - pos), prize=prize
    )


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
    assert "&#9650;" not in html and "&#9660;" not in html


def test_render_report_shows_movement_markers():
    html = render_report(
        [_standing(1, "Rory"), _standing(2, "Kev"), _standing(3, "Sam")],
        season_id="2026-27",
        gameweek=3,
        movement={"Rory": 2, "Kev": -1, "Sam": 0},
    )
    assert "&#9650;&nbsp;2" in html   # up two for Rory
    assert "&#9660;&nbsp;1" in html   # down one for Kev
    assert "&ndash;" in html          # no change for Sam


def test_parse_manager_emails_from_name_email_csv():
    raw = "name,email\nKev Thulbourne,kev@example.com\nRory Canham,rory@example.com\n"
    assert _parse_manager_emails(raw) == ["kev@example.com", "rory@example.com"]


def test_parse_manager_emails_from_plain_list():
    assert _parse_manager_emails("a@x.com, b@y.com ; c@z.com") == [
        "a@x.com",
        "b@y.com",
        "c@z.com",
    ]


def test_parse_manager_emails_newline_list_no_header():
    raw = "thomas.allan@me.com\nthomas.allan@beko.com\n"
    assert _parse_manager_emails(raw) == [
        "thomas.allan@me.com",
        "thomas.allan@beko.com",
    ]


def test_parse_manager_emails_empty():
    assert _parse_manager_emails("") == []
    assert _parse_manager_emails("name,email\n") == []


def test_render_report_prize_positions_use_league_app_colours():
    html = render_report(
        [
            _standing(1, "A", prize=150),
            _standing(2, "B", prize=90),
            _standing(3, "C", prize=70),
            _standing(4, "D", prize=40),   # lower prize position -> amber, like the app
            _standing(5, "E", prize=25),
            _standing(6, "F"),             # no prize -> no accent
        ],
        season_id="2026-27",
    )
    assert "border-left:4px solid #2563eb" in html   # 1st
    assert "border-left:4px solid #15803d" in html   # 2nd
    assert "border-left:4px solid #86efac" in html   # 3rd
    assert "border-left:4px solid #f59e0b" in html   # 4th / 5th
    assert "border-left:4px solid transparent" in html   # 6th, no prize
