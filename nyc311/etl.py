"""Run with python -m nyc311.etl."""

import argparse
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
import sqlite3

import requests

from nyc311.extract import SOURCE_TIMESTAMP, fetch_changes, fetch_requests, row_cursor, utc_text
from nyc311.storage import DEFAULT_DB, commit_ingestion, read_checkpoint, save_requests
from nyc311.transform import clean_requests

LOGGER = logging.getLogger(__name__)
OVERLAP = timedelta(minutes=10)
INITIAL_WINDOW = timedelta(hours=1)
MAX_ADVANCE = timedelta(days=1)


def run(limit: int, db_path: Path) -> int:
    """Fetch, clean, and store one sample without writing partial API results."""
    raw = fetch_requests(limit)
    LOGGER.info("Fetched %d NYC 311 requests.", len(raw))
    cleaned = clean_requests(raw)
    if raw and cleaned.empty:
        raise ValueError("The API returned records, but none passed validation.")
    inserted = save_requests(db_path, cleaned)
    LOGGER.info("Inserted %d requests and skipped %d existing IDs in %s.",
                inserted, len(cleaned) - inserted, db_path)
    return inserted


def run_incremental(
    page_size: int, db_path: Path, *, now: datetime | None = None, since: datetime | None = None
) -> dict:
    """Fetch every page in a fixed UTC window before committing its checkpoint."""
    previous = read_checkpoint(db_path)
    cutoff = now if now is not None else datetime.now(timezone.utc)
    if since is not None:
        if previous is not None:
            raise ValueError("--since is only for starting an ingestion with no checkpoint.")
        if since.utcoffset() is None or since > cutoff:
            raise ValueError("--since must include a time zone and cannot be in the future.")
    if previous is None:
        start = since if since is not None else cutoff - INITIAL_WINDOW
        cutoff = min(cutoff, start + MAX_ADVANCE)
    else:
        checkpoint = datetime.fromisoformat(previous)
        if cutoff < checkpoint:
            raise ValueError("The system clock is behind the ingestion checkpoint.")
        start = checkpoint - OVERLAP
        cutoff = min(cutoff, checkpoint + MAX_ADVANCE)

    LOGGER.info("Scanning NYC 311 modifications from %s through %s.", utc_text(start), utc_text(cutoff))
    raw = []
    for page in fetch_changes(start, cutoff, page_size):
        raw.extend(page)
        LOGGER.info("Fetched %d rows so far.", len(raw))

    # The source is ordered oldest first. Keep the newest valid version per ID.
    raw.reverse()
    cleaned = clean_requests(raw)
    if raw and cleaned.empty:
        raise ValueError(f"All {len(raw)} fetched rows failed validation. Checkpoint unchanged.")
    cleaned[SOURCE_TIMESTAMP] = [utc_text(row_cursor(raw[index])[0]) for index in cleaned.index]

    metrics = commit_ingestion(db_path, cleaned, previous, utc_text(cutoff), len(raw))
    LOGGER.info("Committed checkpoint %s. Quality counts: %s", utc_text(cutoff), metrics)
    return metrics


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--page-size", "--limit", type=int, default=1000,
                        help="Maximum rows per API request (1 to 5000).")
    parser.add_argument("--since", type=datetime.fromisoformat,
                        help="First scan start, as an ISO timestamp with a time zone.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite database path.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        run_incremental(args.page_size, args.db, since=args.since)
    except (requests.RequestException, ValueError, sqlite3.Error, OSError) as error:
        LOGGER.error("ETL failed: %s", error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
