"""Descriptive analytics for the local NYC 311 collection."""

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

STALE_AFTER = timedelta(hours=24)


def freshness(value: pd.Timestamp | None, now: pd.Timestamp) -> str:
    if value is None:
        return "Unavailable"
    if value > now:
        return "Check clock"
    return "Stale" if now - value > STALE_AFTER else "Recent"


def select_records(
    records: pd.DataFrame, dates: tuple[date, date], boroughs: list[str], categories: list[str]
) -> pd.DataFrame:
    days = records["created_date"].dt.date
    mask = (days >= dates[0]) & (days <= dates[1])
    if boroughs:
        mask &= records["borough"].isin(boroughs)
    if categories:
        mask &= records["complaint_type"].isin(categories)
    return records.loc[mask]


def daily_totals(records: pd.DataFrame) -> pd.DataFrame:
    days = records["created_date"].dt.normalize()
    result = days.value_counts(sort=False).sort_index().rename_axis("day").reset_index(name="requests")
    # Break trend lines at dates with no known coverage.
    result["segment"] = result["day"].diff().ne(timedelta(days=1)).cumsum()
    return result


def rankings(records: pd.DataFrame, field: str) -> pd.DataFrame:
    counts = records[field].value_counts().rename_axis("label").reset_index(name="requests")
    counts = counts.sort_values(["requests", "label"], ascending=[False, True])
    counts["share"] = counts["requests"] / len(records)
    return counts


@dataclass(frozen=True)
class WeekComparison:
    previous: int
    latest: int
    start: pd.Timestamp
    end: pd.Timestamp

    @property
    def change(self) -> float:
        return (self.latest - self.previous) / self.previous


def compare_weeks(daily: pd.DataFrame) -> WeekComparison | None:
    """Compare adjacent seven-day periods after excluding observed boundary days."""
    if len(daily) < 16:
        return None
    end = daily["day"].max() - timedelta(days=1)
    dates = pd.date_range(end=end, periods=14)
    if dates[0] <= daily["day"].min():
        return None
    counts = daily.set_index("day")["requests"].reindex(dates)
    if counts.isna().any():
        return None
    previous, latest = int(counts.iloc[:7].sum()), int(counts.iloc[7:].sum())
    if min(previous, latest) < 20:
        return None
    return WeekComparison(previous, latest, dates[0], end)
