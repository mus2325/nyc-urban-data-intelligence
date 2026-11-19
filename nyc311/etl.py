"""Run with python -m nyc311.etl."""

import argparse
import logging
from pathlib import Path
import sqlite3

import requests

from nyc311.extract import fetch_requests
from nyc311.storage import DEFAULT_DB, save_requests
from nyc311.transform import clean_requests

LOGGER = logging.getLogger(__name__)


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=1000, help="Sample size (1 to 5000).")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite database path.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        run(args.limit, args.db)
    except (requests.RequestException, ValueError, sqlite3.Error, OSError) as error:
        LOGGER.error("ETL failed: %s", error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
