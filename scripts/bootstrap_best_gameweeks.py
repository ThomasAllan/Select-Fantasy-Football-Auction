"""One-off script to generate best_gameweeks.csv from all historical data.

Run with:
    uv run python scripts/bootstrap_best_gameweeks.py
"""
from select_football.common.csv_store import CsvStore
from select_football.config import get_settings
from select_football.jobs.sync_scores import _compute_best_gameweeks

settings = get_settings()
store = CsvStore(settings.data_dir)

df = _compute_best_gameweeks(store)
store.write("best_gameweeks", df)
print(f"Written best_gameweeks.csv for {len(df)} managers")
for _, row in df.iterrows():
    print(f"  {row['manager_name']}: {row['label']} ({row['pts']}pts)")
