# Select Football Auction — Claude Context

## What this project is
A custom fantasy football league run among friends. Each season, managers bid at an in-person auction to build a squad of 10 outfield players (DEF/MID/FWD) and 1 Premier League team for their goalkeeper slot. The owner emails a monthly league table and manages everything via CSV files.

## Project layout
```
select_football_auction/
├── select_football/            Python package (installed via uv)
│   ├── config.py               pydantic-settings: SMTP, data_dir, log_level
│   ├── common/
│   │   ├── csv_store.py        All CSV I/O goes through here (read/write/upsert)
│   │   ├── models.py           Pydantic domain models
│   │   └── logging.py          structlog JSON setup
│   ├── core/
│   │   ├── scoring.py          Pure scoring functions (no I/O)
│   │   └── standings.py        Standings engine — takes DataFrames, returns standings
│   ├── fpl/
│   │   ├── client.py           httpx client for FPL API
│   │   ├── models.py           FPL API response dataclasses
│   │   └── sync.py             FPL → CSV sync logic
│   ├── email/
│   │   ├── renderer.py         Jinja2 → HTML report
│   │   ├── sender.py           SMTP dispatch
│   │   └── templates/          monthly_report.html.j2
│   ├── jobs/
│   │   ├── sync_scores.py      CLI: uv run sync-scores [--dry-run]
│   │   └── send_report.py      CLI: uv run send-report [--preview] [--force]
│   └── dashboard/app.py        Streamlit stub (future)
├── data/                       CSV files — gitignored, all persistent state lives here
├── scripts/
│   └── import_selections.py    One-off import from Google Sheets TSV export
└── tests/                      pytest unit tests
```

## Running the project
```
uv sync                         # install deps
uv sync --extra dev             # + dev tools (pytest, ruff, mypy)
uv run sync-scores              # pull FPL data → update CSVs
uv run sync-scores --dry-run    # fetch only, no writes
uv run send-report --preview    # render HTML to stdout, no email sent
uv run send-report              # compute standings → email all managers
uv run pytest                   # run tests
```

## Data files (data/*.csv)
All files use string dtypes throughout — pandas reads them as str. CsvStore.upsert() is idempotent.

| File | Primary key | Notes |
|---|---|---|
| seasons.csv | season_id | season_id format: "2025-26". last_gw_synced set automatically by sync-scores. last_email_sent updated by send-report job. show_in_dashboard="false" hides a season from dashboard filter dropdowns (player history still shows all seasons). |
| managers.csv | name | No ID column — manager name is the key everywhere. |
| manager_selections.csv | player_code + season_id + manager_name + gw_from | gw_to blank = still active. GK rows: player_code = "{season}-team-{fpl_team_id}", position = "GK". No team column — team name is derived dynamically from players.csv for the dashboard. |
| players.csv | code | code = "{season}-{type}-{element_id}". type = player or team. Synced from FPL bootstrap. Includes status/news/news_date columns (A/I/U/D/S). |
| goals.csv | player_code + season_id + game_week | Outfield: goals_scored. GK team rows: goals_conceded. GK player rows: goals_scored (for +4 bonus). |
| overrides.csv | player_code + season_id + game_week | override_points sets the final GW points directly (bypasses position multiplier). |
| prizes.csv | season_id + position | Historical prizes from 2018-19 onwards. |

## Scoring rules
- **DEF**: goals × 3
- **MID**: goals × 2
- **FWD**: goals × 1
- **GK (team slot)**: goals_conceded × (−1), plus if any GK player for that team scored: goals_scored × 4
- **Override**: when an override row exists, override_points is used as the final GW score directly (no position multiplier applied)
- **Active season**: determined from seasons.csv start_date/end_date — no CURRENT_SEASON env var needed
- Sync job skips if GW in progress (FPL: is_current=True, finished=False, data_checked=False)
- Send job skips if last_email_sent in seasons.csv is within the current calendar month

## FPL API
- Base URL: https://fantasy.premierleague.com/api (overridable via FPL_BASE_URL env)
- /bootstrap-static/ — player registry, teams, events
- /element-summary/{id}/ — per-player GW history (goals_scored, goals_conceded)
- /fixtures/?team={id} — team fixtures (used for GK goals conceded)

## Player code convention
`{season_id}-{type}-{element_id}` e.g. `2025-26-player-123`, `2025-26-team-1`
Pre-2023-24 historical data used player names instead of IDs: `2020-21-player-Harry Kane`

## Environment (.env)
```
DATA_DIR=./data
SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASSWORD / EMAIL_FROM
SEND_TEST_ONLY=true/false
TEST_EMAIL=
LOG_LEVEL=INFO
FPL_BASE_URL=https://fantasy.premierleague.com/api
```

## Historical data notes
- Seasons 2018-19 through 2022-23 used name-based player codes
- Seasons 2023-24 onwards use FPL element ID codes
- Historical selections can be imported via `scripts/import_selections.py` from a TSV export of the Google Sheets "Manager Selections" tab
- Some historical managers (Jamie Blunt, Karl Allen, etc.) no longer participate — they appear in old selection rows but may not be in managers.csv

## Tests
```
tests/test_scoring.py       Pure scoring function unit tests
tests/test_standings.py     Full standings engine integration tests
```
Run with: `uv run pytest`
