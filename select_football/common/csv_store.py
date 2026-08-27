from datetime import date
from pathlib import Path

import pandas as pd


class CsvStore:
    """Single choke point for all CSV reads and writes.

    All methods resolve file paths relative to the configured data_dir so the
    rest of the application never needs to know where files live on disk.
    """

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        fname = name if name.endswith(".csv") else f"{name}.csv"
        return self.data_dir / fname

    def read(self, name: str) -> pd.DataFrame:
        """Return all rows from a CSV as a DataFrame.

        Returns an empty DataFrame (with headers from the file) if the file
        exists but has no data rows. Returns an empty DataFrame with no columns
        if the file does not exist yet.
        """
        path = self._path(name)
        if not path.exists():
            return pd.DataFrame()
        return pd.read_csv(path, dtype=str).fillna("")

    def write(self, name: str, df: pd.DataFrame) -> None:
        """Overwrite the CSV with the provided DataFrame."""
        path = self._path(name)
        df.to_csv(path, index=False)

    def upsert(self, name: str, new_df: pd.DataFrame, key_cols: list[str]) -> None:
        """Merge new_df into the existing CSV using key_cols as the primary key.

        Existing rows whose key matches a row in new_df are replaced.
        Rows in new_df with no existing match are appended.
        Rows in the existing CSV with no match in new_df are kept unchanged.

        This operation is idempotent: running it twice with the same data
        produces the same result.

        An empty new_df is a no-op: there is nothing to merge, and it carries
        no columns to validate key_cols against.
        """
        if new_df.empty:
            return

        existing = self.read(name)

        if existing.empty:
            self.write(name, new_df)
            return

        # Align dtypes for the merge — both sides must be str for key columns
        for col in key_cols:
            if col in existing.columns:
                existing[col] = existing[col].astype(str)
            if col in new_df.columns:
                new_df = new_df.copy()
                new_df[col] = new_df[col].astype(str)

        # Drop existing rows that are superseded by incoming rows
        merged = existing.merge(
            new_df[key_cols].drop_duplicates(),
            on=key_cols,
            how="left",
            indicator=True,
        )
        kept = existing[merged["_merge"] == "left_only"]

        result = pd.concat([kept, new_df], ignore_index=True)
        self.write(name, result)

    def read_all_players(self) -> pd.DataFrame:
        """Return all player and team records from players.csv."""
        return self.read("players")

    def is_season_closed(self, season_id: str) -> bool:
        """Return True if this season is marked closed=true in seasons.csv."""
        seasons = self.read("seasons")
        if seasons.empty or "closed" not in seasons.columns:
            return False
        row = seasons[seasons["season_id"] == season_id]
        if row.empty:
            return False
        return str(row["closed"].iloc[0]).strip().lower() == "true"

    def current_season(self) -> str:
        """Return the season_id for the active or next upcoming open season.

        First looks for an open season whose date window contains today.
        If none found (pre-season gap), returns the nearest upcoming open season
        so that player data can be synced before the season officially starts.
        Raises RuntimeError only if no open season exists at all.
        """
        df = self.read("seasons")
        if df.empty or "season_id" not in df.columns:
            raise RuntimeError("seasons.csv is empty or missing — cannot determine current season")

        today = date.today()
        upcoming: list[tuple[date, str]] = []

        for _, row in df.iterrows():
            if str(row.get("closed", "")).strip().lower() == "true":
                continue
            try:
                start = date.fromisoformat(str(row["start_date"]))
                end = date.fromisoformat(str(row["end_date"]))
            except ValueError:
                continue
            if start <= today <= end:
                return str(row["season_id"])
            if start > today:
                upcoming.append((start, str(row["season_id"])))

        if upcoming:
            upcoming.sort()
            return upcoming[0][1]

        raise RuntimeError(
            f"No open season in seasons.csv covers or follows today ({today}). "
            "Add a new season row to seasons.csv to enable syncing."
        )
