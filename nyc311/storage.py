"""Store and read cleaned requests in SQLite."""

from contextlib import closing
from pathlib import Path
import sqlite3

import pandas as pd

from nyc311.extract import FIELDS

DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "nyc311.sqlite3"


def save_requests(db_path: Path, records: pd.DataFrame) -> int:
    """Insert new requests in one transaction and return the number inserted."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS requests (
                unique_key TEXT PRIMARY KEY NOT NULL,
                created_date TEXT NOT NULL,
                complaint_type TEXT NOT NULL,
                borough TEXT NOT NULL
            )
        """)
        cursor = connection.executemany("""
            INSERT INTO requests (unique_key, created_date, complaint_type, borough)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(unique_key) DO NOTHING
        """, records[FIELDS].itertuples(index=False, name=None))
        return cursor.rowcount


def load_requests(db_path: Path) -> pd.DataFrame:
    """Open SQLite in read-only mode so a dashboard cannot create or mutate it."""
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        return pd.read_sql_query(
            "SELECT unique_key, created_date, complaint_type, borough FROM requests",
            connection,
            parse_dates=["created_date"],
        )
