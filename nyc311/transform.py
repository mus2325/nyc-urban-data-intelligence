"""Validate source records and normalize the fields used by the dashboard."""

import logging

import pandas as pd

from nyc311.extract import FIELDS

LOGGER = logging.getLogger(__name__)
BOROUGHS = {"BRONX", "BROOKLYN", "MANHATTAN", "QUEENS", "STATEN ISLAND"}


def clean_requests(rows: list[dict]) -> pd.DataFrame:
    """Drop unusable rows and keep the first valid record for each request ID."""
    frame = pd.DataFrame.from_records(rows, columns=FIELDS)

    # The source timestamps are local NYC times. Keep them local for daily charts.
    dates = frame["created_date"].map(lambda value: value if isinstance(value, str) else None)
    local_timestamp = dates.astype("string").str.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?",
        na=False,
    )
    dates = pd.to_datetime(dates.where(local_timestamp), format="ISO8601", errors="coerce")
    keys = frame["unique_key"].astype("string").str.strip().str.lstrip("0")
    categories = frame["complaint_type"].map(
        lambda value: value.strip() if isinstance(value, str) else ""
    )
    valid = keys.str.fullmatch(r"[1-9][0-9]*", na=False) & dates.notna() & categories.ne("")
    LOGGER.info("Rejected %d rows with a missing or invalid ID, date, or category.", (~valid).sum())

    boroughs = frame["borough"].astype("string").str.strip().str.upper()
    frame["unique_key"] = keys
    frame["created_date"] = dates.dt.strftime("%Y-%m-%dT%H:%M:%S.%f")
    frame["complaint_type"] = categories
    frame["borough"] = boroughs.where(boroughs.isin(BOROUGHS), "UNKNOWN")
    accepted = frame.loc[valid]
    result = accepted.drop_duplicates(subset="unique_key", keep="first")
    LOGGER.info("Removed %d duplicate IDs within the sample.", len(accepted) - len(result))
    return result
