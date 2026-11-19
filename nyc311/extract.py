"""Fetch a bounded sample from the public NYC Open Data API."""

import requests

API_URL = "https://data.cityofnewyork.us/resource/erm2-nwe9.json"
FIELDS = ["unique_key", "created_date", "complaint_type", "borough"]
MAX_SAMPLE_SIZE = 5_000


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
    rows = response.json()
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("NYC Open Data returned an unexpected response format.")
    return rows
