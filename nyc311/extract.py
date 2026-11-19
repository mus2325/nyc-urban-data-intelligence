"""Fetch NYC 311 rows with bounded requests to the public API."""

from collections.abc import Iterator
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import logging
import re
import time

import requests

API_URL = "https://data.cityofnewyork.us/resource/erm2-nwe9.json"
FIELDS = ["unique_key", "created_date", "complaint_type", "borough"]
MAX_SAMPLE_SIZE = 5_000
MODIFIED_FIELD = ":updated_at"
SOURCE_TIMESTAMP = "source_updated_at"
# Compare numeric IDs even if the API serializes them as text.
SORT_KEY = "unique_key::number"
LOGGER = logging.getLogger(__name__)


def fetch_requests(limit: int) -> list:
    """Fetch the latest requests with stable ordering when timestamps tie."""
    if not 1 <= limit <= MAX_SAMPLE_SIZE:
        raise ValueError(f"Sample size must be between 1 and {MAX_SAMPLE_SIZE:,}.")

    response = requests.get(
        API_URL,
        params={
            "$select": ",".join(FIELDS),
            "$order": "created_date DESC, unique_key DESC",
            "$limit": limit,
        },
        timeout=(5, 30),
    )
    response.raise_for_status()
    return response_rows(response)


def response_rows(response: requests.Response) -> list[dict]:
    rows = response.json()
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("NYC Open Data returned an unexpected response format.")
    return rows


def utc_text(value: datetime) -> str:
    """Represent an aware timestamp in a fixed-width, sortable UTC format."""
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def row_cursor(row: dict) -> tuple[datetime, int]:
    timestamp = row.get(SOURCE_TIMESTAMP)
    key = str(row.get("unique_key"))
    if not isinstance(timestamp, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
        r"(?:\.[0-9]{1,6}0*)?(?:Z|[+-][0-9]{2}:[0-9]{2})",
        timestamp,
    ):
        raise ValueError("Missing or unsupported source_updated_at value from NYC Open Data.")
    if not re.fullmatch(r"0*[1-9][0-9]*", key):
        raise ValueError("A source row has no valid numeric ID for pagination.")
    return datetime.fromisoformat(timestamp), int(key)


def retry_delay(header: str | None, attempt: int) -> float:
    """Honor Retry-After as seconds or an HTTP date when the server sends it."""
    if header:
        if header.isascii() and header.isdecimal():
            return int(header)
        try:
            return max(0, (parsedate_to_datetime(header) - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError):
            pass
    return 2 ** attempt


def fetch_page(params: dict) -> list[dict]:
    for attempt in range(3):
        try:
            response = requests.get(API_URL, params=params, timeout=(5, 30))
            response.raise_for_status()
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as error:
            failed_response = error.response
            if failed_response is None:
                if isinstance(error, requests.exceptions.ProxyError):
                    raise
                header = None
            else:
                status = failed_response.status_code
                if status == 400:
                    raise ValueError(
                        "NYC Open Data rejected the incremental query. "
                        "Verify that erm2-nwe9 exposes :updated_at and a numeric unique_key."
                    ) from error
                if status != 429 and not 500 <= status < 600:
                    raise
                header = failed_response.headers.get("Retry-After")
            if attempt == 2:
                raise
            delay = retry_delay(header, attempt)
            if delay > 30:
                raise ValueError(f"NYC Open Data requested a {delay:g}s wait. Rerun later.") from error
            LOGGER.warning("NYC Open Data request failed (%s). Retrying in %.1fs.", error, delay)
            time.sleep(delay)
        else:
            return response_rows(response)


def fetch_changes(start: datetime, end: datetime, page_size: int) -> Iterator[list[dict]]:
    """Read an inclusive window in timestamp/ID order using keyset pagination."""
    if not 1 <= page_size <= MAX_SAMPLE_SIZE:
        raise ValueError(f"Page size must be between 1 and {MAX_SAMPLE_SIZE:,}.")

    cursor = None
    window = f"{MODIFIED_FIELD} between '{utc_text(start)}' and '{utc_text(end)}'"
    while True:
        where = window
        if cursor is not None:
            timestamp, key = cursor
            stamp = utc_text(timestamp)
            where += (f" AND ({MODIFIED_FIELD} > '{stamp}' OR "
                      f"({MODIFIED_FIELD} = '{stamp}' AND {SORT_KEY} > {key}))")

        page = fetch_page({
            "$select": ",".join([*FIELDS, f"{MODIFIED_FIELD} AS {SOURCE_TIMESTAMP}"]),
            "$where": where,
            "$order": f"{MODIFIED_FIELD} ASC, {SORT_KEY} ASC",
            "$limit": page_size,
        })
        if len(page) > page_size:
            raise ValueError("NYC Open Data returned more rows than the requested page size.")
        for row in page:
            position = row_cursor(row)
            if not start <= position[0] <= end or (cursor is not None and position <= cursor):
                raise ValueError("NYC Open Data returned rows outside the requested window or order.")
            cursor = position
        if page:
            yield page
        if len(page) < page_size:
            break
