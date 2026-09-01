# Season selection imports

Drop this season's auction workbook here — a single `FOOTY20XX.xlsx` file — then run:

```
uv run sync-scores                          # first, so players.csv has the new season
uv run python scripts/import_season.py      # no argument needed
```

The script reads the one `.xlsx` file in this folder and matches every player to
`data/players.csv`. **Every player and GK team must match** — if even one row is
unmatched a real run writes nothing, keeps the workbook, and exits with an error.
Once the whole season resolves cleanly it writes the manager selections into
`data/manager_selections.csv` and **deletes the workbook**.

Always run it with `--dry-run` first to check the matching, and add `--auto` to
let it accept the obvious ones for you. A dry run never writes, deletes, or
errors out — it just shows you where things stand:

```
uv run python scripts/import_season.py --dry-run --auto
```

`--auto` fills in an unmatched player when there's exactly one confident
same-club name match — typos like `Mbuemo → Mbeumo`, `Wirz → Wirtz`,
`Odegaard → Ødegaard`. Every auto-pick is printed under **"CHECK THESE"** so you
can eyeball them; anything it isn't sure about still goes to `fixes.csv` (below).

At the top of every run the script prints:
- **Squad shape** it's using (e.g. `1 GK + 3 DEF + 5 MID + 2 FWD`). It comes from
  `SEASON_SHAPES` in `scripts/import_season.py`; override for one run with
  `--shape 3-5-2`. This decides each row's position, so check it's right.
- A **sync reminder** if `players.csv` for the season is more than a few days old.

Before a real import it verifies the whole thing and reports:
- **PROBLEMS** (block a real import; a dry run just lists them):
  - a matched player whose **FPL club doesn't match column B** — e.g.
    `E.Fernandes @ Chelsea` matching Mateus Fernandes (Spurs). If the match is
    wrong, pin the right code in `fixes.csv`. If the player genuinely changed
    club since the auction, **pin the same code in `fixes.csv` anyway** — a
    code in the fixes file counts as "I've checked this", and the mismatch drops
    to a note instead of blocking. (Or just fix column B in the workbook.)
  - the same player or GK team picked by **more than one manager**.
  - a squad that costs **more** than the `--budget` (default 100).
- **WARNINGS** (shown, don't block unless `--strict`): a squad whose row costs
  don't add up to the sheet's own total.
- FYI lines: club mismatches you've accepted via `fixes.csv`, and **underspent**
  managers (squad cost *below* budget).

Notes:
- One workbook at a time. If several are here the script stops and asks you to leave just one.
- Old `.xls` files aren't supported — open in Excel, "Save As" `.xlsx`, then re-run.
- Everything in this folder except this README is gitignored.

## Fixing what the run flags: `import/fixes.csv`

Whenever a run has something to resolve — an **unmatched** row, or a **PROBLEM**
(club mismatch or duplicate pick) — it writes every one of those rows to
`import/fixes.csv` for you. A dry run still writes the file (and exits 0); a real
run refuses until it's clean.

```
manager,position,excel_name,excel_team,player_code,note,suggestions
Thomas Allan,DEF,V.Van Dijk,Liverpool,,unmatched,2026-27-player-356  Virgil van Dijk (Liverpool) | ...
Sam Kennedy,MID,E.Fernandes,Chelsea,2026-27-player-525,"club? matched Spurs, sheet says 'Chelsea'; duplicate 2× — change one",2026-27-player-155  Enzo Fernández (Chelsea) | ...
Sam Kennedy,MID,Y.Tielemans,Aston Villa,2026-27-player-43,"club? matched Man Utd, sheet says 'Aston Villa'",2026-27-player-43  Youri Tielemans (Man Utd) | ...
```

- **`note`** tells you why the row is there.
- **`player_code`** is **blank** for an unmatched row, or **pre-filled with the
  current guess** for a club-mismatch / duplicate row.
  - *Leave it* to accept that guess (e.g. `Y.Tielemans` — right player, just
    changed club since the auction).
  - *Change it* to fix a wrong match (e.g. `E.Fernandes` → put Enzo's code
    `2026-27-player-155`, not Mateus Fernandes).
- **`suggestions`** lists likely players + codes — usually the first is right.
  For a GK team it's a `2026-27-team-1` style code.
- `manager`, `position`, `note`, `suggestions` are only there to help you — the
  script reads back just `excel_name`, `excel_team`, `player_code`.

Then re-run (keep `--auto`, add `--dry-run` to check first):

```
uv run python scripts/import_season.py --dry-run --auto --overrides import/fixes.csv
uv run python scripts/import_season.py --auto --overrides import/fixes.csv
```

Repeat until the run says **"all checks passed"**, then let it write. An existing
`fixes.csv` is **never overwritten** while you're working — your edits are safe
across runs. On the successful write, both the workbook **and `import/fixes.csv`
are deleted** (its contents are now in `data/manager_selections.csv`). A fixes
file you passed from some other path is left alone.

### If a whole club is mis-spelled

If several players from the same club fail because the workbook uses an unusual
spelling or abbreviation, add that spelling to `scripts/team_aliases.py`
(key = lower-case spelling, value = the FPL `full_name`). That fixes every row
for that club at once.
