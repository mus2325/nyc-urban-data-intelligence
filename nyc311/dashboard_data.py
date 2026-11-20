"""Read a consistent dashboard snapshot from either SQLite schema."""

from contextlib import closing
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3

import pandas as pd

from nyc311.extract import FIELDS, SOURCE_TIMESTAMP


@dataclass
class DashboardData:
    records: pd.DataFrame
    source_latest: pd.Timestamp | None
    watermark: pd.Timestamp | None = None
    last_success: pd.Timestamp | None = None
    counts: dict[str, int] | None = None


def timestamp(value: str | None) -> pd.Timestamp | None:
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    return None if pd.isna(parsed) else parsed


def load_dashboard(path: Path) -> DashboardData:
    uri = f"{path.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        # Read the rows and checkpoint from the same transaction.
        connection.execute("BEGIN")
        columns = {row[1] for row in connection.execute("PRAGMA table_info(requests)")}
        fields = ", ".join(FIELDS)
        records = pd.read_sql_query(f"SELECT {fields} FROM requests", connection)
        records["created_date"] = pd.to_datetime(
            records["created_date"], format="ISO8601", errors="coerce"
        )
        latest = None
        if SOURCE_TIMESTAMP in columns:
            latest, = connection.execute("SELECT MAX(source_updated_at) FROM requests").fetchone()
        snapshot = DashboardData(records, timestamp(latest))
        state_table = connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = 'ingestion_state'"
        ).fetchone()
        if state_table:
            state = connection.execute(
                "SELECT watermark, last_successful_run, quality_counts FROM ingestion_state WHERE id = 1"
            ).fetchone()
            if state:
                watermark, last_success, raw_counts = state
                snapshot.watermark = timestamp(watermark)
                snapshot.last_success = timestamp(last_success)
                try:
                    counts = json.loads(raw_counts)
                except (TypeError, ValueError):
                    counts = None
                if isinstance(counts, dict):
                    snapshot.counts = {
                        key: value for key, value in counts.items()
                        if type(value) is int and value >= 0
                    }
        return snapshot
