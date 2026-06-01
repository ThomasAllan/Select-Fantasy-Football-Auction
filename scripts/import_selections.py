"""One-off script to import manager selections from a TSV/CSV export of Google Sheets.

Usage:
    uv run python scripts/import_selections.py selections.tsv
    uv run python scripts/import_selections.py selections.csv

The input file should have these columns (tab or comma separated, with header row):
    Player Code | Season | Manager | Player Name | Position | Cost | GW From | GW To (Inc) | Team

Rows with position GKG are skipped (legacy tracking artefacts).
Cost values like "£3.00" are cleaned to "3".
Output is appended/upserted into data/manager_selections.csv.
"""
import re
import sys
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd

from select_football.common.csv_store import CsvStore
from select_football.config import get_settings


def clean_cost(val: str) -> str:
    cleaned = re.sub(r"[£$,\s]", "", str(val))
    try:
        return str(float(cleaned))
    except ValueError:
        return "0"


def run(input_path: str) -> None:
    path = Path(input_path)
    sep = "\t" if path.suffix.lower() == ".tsv" else ","

    df = pd.read_csv(path, sep=sep, dtype=str).fillna("")

    # Normalise column names
    df.columns = [c.strip() for c in df.columns]
    col_map = {
        "Player Code": "player_code",
        "Season": "season_id",
        "Manager": "manager_name",
        "Position": "position",
        "Cost": "cost",
        "GW From": "gw_from",
        "GW To (Inc)": "gw_to",
    }
    df = df.rename(columns=col_map)

    # Keep only expected columns
    expected = list(col_map.values())
    for col in expected:
        if col not in df.columns:
            df[col] = ""
    df = df[expected]

    # Drop legacy GKG rows
    skipped = df[df["position"].str.upper() == "GKG"]
    if not skipped.empty:
        print(f"Skipping {len(skipped)} GKG rows: {skipped['player_name'].tolist()}")
    df = df[df["position"].str.upper() != "GKG"]

    # Clean cost
    df["cost"] = df["cost"].apply(clean_cost)

    # Drop rows with no player_code
    df = df[df["player_code"].str.strip() != ""]

    print(f"Importing {len(df)} rows...")

    settings = get_settings()
    store = CsvStore(settings.data_dir)
    store.upsert(
        "manager_selections",
        df,
        key_cols=["player_code", "season_id", "manager_name", "gw_from"],
    )

    print("Done. Rows in manager_selections.csv:", len(store.read("manager_selections")))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: python {sys.argv[0]} <input.tsv|input.csv>")
        sys.exit(1)
    run(sys.argv[1])
