"""Store and read cleaned requests in SQLite."""

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

import pandas as pd

from nyc311.extract import FIELDS, SOURCE_TIMESTAMP, utc_text

DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "nyc311.sqlite3"


def create_requests_table(connection: sqlite3.Connection) -> None:
    connection.execute("""
        CREATE TABLE IF NOT EXISTS requests (
            unique_key TEXT PRIMARY KEY NOT NULL,
            created_date TEXT NOT NULL,
            complaint_type TEXT NOT NULL,
            borough TEXT NOT NULL
        )
    """)


def save_requests(db_path: Path, records: pd.DataFrame) -> int:
    """Insert new requests in one transaction and return the number inserted."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as connection, connection:
        create_requests_table(connection)
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


def read_checkpoint(db_path: Path) -> str | None:
    """Return the last scanned time, including for a database created by V1."""
    if not db_path.is_file():
        return None
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        return checkpoint_from(connection)


def checkpoint_from(connection: sqlite3.Connection) -> str | None:
    table = connection.execute(
        "SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = 'ingestion_state'"
    ).fetchone()
    if table is not None:
        row = connection.execute("SELECT watermark FROM ingestion_state WHERE id = 1").fetchone()
        if row is not None:
            return row[0]
    return None


def commit_ingestion(
    db_path: Path, records: pd.DataFrame, previous: str | None, watermark: str, fetched: int
) -> dict:
    """Upsert data, quality counts, and the new checkpoint in one transaction."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    metrics = {"fetched": fetched, "accepted": len(records), "rejected": fetched - len(records),
               "new": 0, "updated": 0, "unchanged": 0}
    with closing(sqlite3.connect(db_path)) as connection, connection:
        # Lock before checking state so two ingestion runs cannot commit out of order.
        connection.execute("BEGIN IMMEDIATE")
        if checkpoint_from(connection) != previous:
            raise ValueError("The checkpoint changed during ingestion. Rerun from the new checkpoint.")
        create_requests_table(connection)
        columns = {column[1] for column in connection.execute("PRAGMA table_info(requests)")}
        if SOURCE_TIMESTAMP not in columns:
            connection.execute("ALTER TABLE requests ADD COLUMN source_updated_at TEXT")

        for row in records[[*FIELDS, SOURCE_TIMESTAMP]].itertuples(index=False, name=None):
            current = connection.execute(
                "SELECT created_date, complaint_type, borough, source_updated_at "
                "FROM requests WHERE unique_key = ?",
                (row[0],),
            ).fetchone()
            result = connection.execute("""
                INSERT INTO requests
                    (unique_key, created_date, complaint_type, borough, source_updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(unique_key) DO UPDATE SET
                    created_date = excluded.created_date,
                    complaint_type = excluded.complaint_type,
                    borough = excluded.borough,
                    source_updated_at = excluded.source_updated_at
                WHERE requests.source_updated_at IS NULL
                   OR excluded.source_updated_at > requests.source_updated_at
                   OR (excluded.source_updated_at = requests.source_updated_at AND (
                       excluded.created_date != requests.created_date
                       OR excluded.complaint_type != requests.complaint_type
                       OR excluded.borough != requests.borough
                   ))
            """, row)
            if current is None:
                metrics["new"] += 1
            elif result.rowcount and current[:3] != row[1:4]:
                metrics["updated"] += 1
            else:
                metrics["unchanged"] += 1

        connection.execute("""
            CREATE TABLE IF NOT EXISTS ingestion_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                watermark TEXT NOT NULL,
                last_successful_run TEXT NOT NULL,
                quality_counts TEXT NOT NULL
            )
        """)
        metrics["last_successful_run"] = utc_text(datetime.now(timezone.utc))
        connection.execute("""
            INSERT INTO ingestion_state (id, watermark, last_successful_run, quality_counts)
            VALUES (1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                watermark = excluded.watermark,
                last_successful_run = excluded.last_successful_run,
                quality_counts = excluded.quality_counts
        """, (watermark, metrics["last_successful_run"], json.dumps(metrics)))
    return metrics
