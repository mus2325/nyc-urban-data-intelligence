"""Run with python -m nyc311.etl."""

import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

import requests

from nyc311.extract import SOURCE_TIMESTAMP, fetch_changes, fetch_requests, row_cursor, utc_text
from nyc311.storage import (
    DEFAULT_DB,
    commit_ingestion,
    ensure_incremental_schema,
    read_checkpoint,
    save_requests,
    stage_page,
)
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
    page_size: int,
    db_path: Path,
    *,
    now: datetime | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    validate_page: bool = False,
) -> dict:
    """Fetch every page in a fixed UTC window before committing its checkpoint."""
    previous = None if validate_page else read_checkpoint(db_path)
    cutoff = now if now is not None else datetime.now(timezone.utc)
    if until is not None:
        if until.utcoffset() is None or until > cutoff:
            raise ValueError("--until must include a time zone and cannot be in the future.")
        cutoff = until
    if since is not None:
        if previous is not None:
            raise ValueError("--since is only for starting an ingestion with no checkpoint.")
        if since.utcoffset() is None or since > cutoff:
            raise ValueError("--since must include a time zone and cannot be after the scan end.")
    if previous is None:
        start = since if since is not None else cutoff - INITIAL_WINDOW
        cutoff = min(cutoff, start + MAX_ADVANCE)
    else:
        checkpoint = datetime.fromisoformat(previous)
        if cutoff < checkpoint:
            raise ValueError("The scan end is behind the ingestion checkpoint.")
        start = checkpoint - OVERLAP
        cutoff = min(cutoff, checkpoint + MAX_ADVANCE)

    LOGGER.info("Scanning NYC 311 modifications from %s through %s.", utc_text(start), utc_text(cutoff))
    # A private on-disk database holds the scan while the API is being read.
    # Production SQLite is only opened for writes after pagination completes.
    with TemporaryDirectory(prefix="nyc311-stage-") as directory:
        stage_path = Path(directory) / "staged.sqlite3"
        with closing(sqlite3.connect(stage_path)) as stage:
            ensure_incremental_schema(stage)
            fetched = 0
            for page in fetch_changes(start, cutoff, page_size):
                fetched += len(page)
                # Pages arrive oldest first. Reverse one page so validation keeps
                # its newest valid version, then merge it with earlier pages.
                page = page[::-1]
                cleaned = clean_requests(page)
                cleaned[SOURCE_TIMESTAMP] = [
                    utc_text(row_cursor(page[index])[0]) for index in cleaned.index
                ]
                stage_page(stage, cleaned)
                LOGGER.info("Fetched %d rows so far.", fetched)
                if validate_page:
                    break

            accepted, = stage.execute("SELECT COUNT(*) FROM requests").fetchone()
            metrics = {"fetched": fetched, "accepted": accepted, "rejected": fetched - accepted}
            if validate_page:
                LOGGER.info("Validation only. No completed window recorded. Counts: %s", metrics)
            if fetched and not accepted:
                raise ValueError(f"All {fetched} fetched rows failed validation. Checkpoint unchanged.")
            if validate_page:
                return metrics

            # Iterating a SQLite cursor streams rows instead of rebuilding the scan in memory.
            rows = stage.execute("""
                SELECT unique_key, created_date, complaint_type, borough, source_updated_at
                FROM requests ORDER BY unique_key
            """)
            metrics = commit_ingestion(db_path, rows, previous, utc_text(cutoff), metrics)
    LOGGER.info("Committed checkpoint %s. Quality counts: %s", utc_text(cutoff), metrics)
    return metrics


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--page-size", "--limit", type=int, default=1000,
                        help="Maximum rows per API request (1 to 5000).")
    parser.add_argument("--since", type=datetime.fromisoformat,
                        help="First scan start, as an ISO timestamp with a time zone.")
    parser.add_argument("--until", type=datetime.fromisoformat,
                        help="Optional scan end, as an ISO timestamp with a time zone.")
    parser.add_argument("--validate-page", action="store_true",
                        help="Validate up to one page without opening the target database.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite database path.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        run_incremental(args.page_size, args.db, since=args.since, until=args.until,
                        validate_page=args.validate_page)
    except KeyboardInterrupt:
        LOGGER.warning("ETL interrupted. An incomplete window is never checkpointed.")
        return 130
    except (requests.RequestException, ValueError, sqlite3.Error, OSError) as error:
        LOGGER.error("ETL failed: %s", error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
