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
├── import/                     Drop a FOOTY20XX.xlsx here; import_season.py loads it, then deletes it
├── scripts/
│   ├── import_season.py        Load manager selections from a FOOTY20XX.xlsx workbook
│   ├── team_aliases.py         Team-name spellings → canonical FPL full_name (used by import_season)
│   ├── manager_names.py        Sheet-name → canonical manager-name aliases
│   └── keep_app_awake.py       Pings the Streamlit app (keep-alive workflow)
└── tests/                      pytest unit tests
```

## Running the project
```
uv sync                         # install deps
uv sync --extra dev             # + dev tools (pytest, ruff, mypy)
uv run sync-scores              # pull FPL data → update CSVs
uv run sync-scores --dry-run    # fetch only, no writes
uv run send-report --preview    # render HTML to stdout, no email sent
uv run send-report --to me@x.com  # test send to just that address (guards off, not recorded)
uv run send-report              # compute standings → email all managers
uv run pytest                   # run tests
```

## Importing a new season's selections
1. `uv run sync-scores` — populates `players.csv` for the new season
2. Drop the auction workbook (`FOOTY20XX.xlsx`) into `import/`
3. `uv run python scripts/import_season.py --dry-run --auto` — preview
4. Resolve anything still unmatched (see below), then run without `--dry-run`:
   writes `data/manager_selections.csv` and deletes the workbook from `import/`

**Squad shape** (which rows are DEF/MID/FWD) comes from `SEASON_SHAPES` in
`scripts/import_season.py` — `2026-27` is `(3, 5, 2)`; add a tuple per season, or
override once with `--shape 3-5-2`. It's printed at the top of every run.

The import is **all-or-nothing**: if any player or GK team is unmatched it writes
nothing, keeps the workbook, and exits non-zero (a `--dry-run` with unmatched
rows too). It writes the unmatched rows to `import/fixes.csv` — blank
`player_code` column plus a `suggestions` column; an existing file is never
overwritten. `--auto` accepts confident single same-club name matches itself
(printed for review). Fill in remaining codes and re-run with
`--overrides import/fixes.csv --auto`, or add a bad club spelling to
`scripts/team_aliases.py`.

Before a real import it verifies and blocks on: a matched player whose **FPL club
differs from column B** on the sheet (pin that `player_code` in the fixes CSV to
accept it — an override match counts as "checked" and the mismatch becomes a
note), a **duplicate pick** (one player/GK team in two squads), or a squad **over
`--budget`** (default 100). A squad whose costs don't match the sheet's own total
is a warning (`--strict` makes warnings block). `--dry-run` lists all of this and
exits 0.
Explicit path (`... import_season.py FOOTY2026.xlsx`) skips `import/` and never
deletes. Only `import/README.md` is tracked in git; workbooks and `fixes.csv` are
gitignored. See `import/README.md` and the header of `scripts/import_season.py`.

## Data files (data/*.csv)
All files use string dtypes throughout — pandas reads them as str. CsvStore.upsert() is idempotent.
All persistent state lives here and is tracked in git. Manager email addresses are **not** a data file — they live only in the `MANAGER_EMAILS` secret (env var locally) and are read straight from there by send-report.

`data/config.json` (not a CSV, tracked) holds `last_email_sent` (ISO date) — written by send-report on a real send, committed back by the workflow.

| File | Primary key | Notes |
|---|---|---|
| seasons.csv | season_id | season_id format: "2025-26". last_gw_synced set automatically by sync-scores. last_synced_at (UTC ISO timestamp) set by sync-scores on every real run, used by the dashboard's "Last updated" display. show_in_dashboard="false" hides a season from dashboard filter dropdowns (player history still shows all seasons). |
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
- Sync job runs continuously, including while a GW is in progress, so the live table reflects partial goals; it skips only in true pre-season, before any GW has started (override with `--force`)
- Send job skips if the monthly email already went out this calendar month, or if a GW is in progress (FPL: is_current=True, finished=False, data_checked=False)

## FPL API
- Base URL: https://fantasy.premierleague.com/api (overridable via FPL_BASE_URL env)
- /bootstrap-static/ — player registry, teams, events
- /element-summary/{id}/ — per-player GW history (goals_scored, goals_conceded)
- /fixtures/?team={id} — team fixtures (used for GK goals conceded)

## Player code convention
`{season_id}-{type}-{element_id}` e.g. `2025-26-player-123`, `2025-26-team-1`
Pre-2023-24 historical data used player names instead of IDs: `2020-21-player-Harry Kane`

## Environment (.env)
Copy `.env.example` → `.env` for local runs (gitignored). In CI these come from repo secrets.
```
DATA_DIR=./data
SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASSWORD / EMAIL_FROM
MANAGER_EMAILS=                  # report recipients; "name,email" CSV text or a plain address list
SEND_TEST_ONLY=true/false        # local only — send just to TEST_EMAIL
TEST_EMAIL=
LOG_LEVEL=INFO
FPL_BASE_URL=https://fantasy.premierleague.com/api
```

## GitHub Actions
| Workflow | Schedule | What it does |
|---|---|---|
| `.github/workflows/sync-scores.yml` | daily 06:00 UTC | `uv run sync-scores`, commits updated `data/*.csv` |
| `.github/workflows/send-report.yml` | daily 07:00 UTC | `uv run send-report`, commits `data/config.json` marker |
| `.github/workflows/keep-alive.yml` | every 8h | pings the Streamlit app so it doesn't sleep |

**send-report** runs daily but sends at most once per calendar month: it no-ops
unless the `last_email_sent` marker is from a previous month, no gameweek is in
progress, and the active season has standings. In practice the email goes out on
the first settled (no gameweek live) day of each new month; every other day is a
clean no-op. Manual runs: Actions → "Send monthly report" → Run
workflow, `run_mode` = `send` / `force` (ignore the monthly guard) / `preview`
(render to log, no email). Set `test_recipient` to an address to send a one-off
`[TEST]` copy to just that person — guards off, send not recorded, so the real
monthly email still goes out later. It overrides `run_mode`.

Recipients live **only** in the `MANAGER_EMAILS` secret, read directly by
send-report (no file is written). The value can be the `name,email` CSV text or a
plain comma/semicolon/newline-separated list of addresses — any token without an
`@` is ignored. Edit that secret to add/remove a manager. Required secrets:
`MANAGER_EMAILS`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_FROM`.

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
