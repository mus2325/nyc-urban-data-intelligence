from contextlib import closing
from datetime import date
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
from streamlit.testing.v1 import AppTest

from nyc311.analytics import compare_weeks, daily_totals, freshness, rankings, select_records
from nyc311.dashboard_data import load_dashboard
from nyc311.storage import commit_ingestion, save_requests
from nyc311.transform import clean_requests
from tests.test_pipeline import request_row


class AnalyticsTests(unittest.TestCase):
    def test_filters_are_inclusive_and_combine(self):
        records = pd.DataFrame({
            "created_date": pd.to_datetime(["2025-01-01 00:00", "2025-01-02 23:59",
                                           "2025-01-02 12:00", "2025-01-03 00:00", None]),
            "borough": ["QUEENS", "QUEENS", "BRONX", "QUEENS", "QUEENS"],
            "complaint_type": ["Noise", "Noise", "Noise", "Heat", "Noise"],
        })
        dates = (date(2025, 1, 1), date(2025, 1, 2))
        selection = select_records(records, dates, ["QUEENS"], ["Noise"])
        self.assertEqual(selection.index.tolist(), [0, 1])
        self.assertEqual(select_records(records, dates, [], []).index.tolist(), [0, 1, 2])

    def test_rankings_are_deterministic_and_use_selection_as_denominator(self):
        records = pd.DataFrame({"complaint_type": ["Noise", "Heat", "Noise", "Heat", "Water"]})
        result = rankings(records, "complaint_type")
        self.assertEqual(result["label"].tolist(), ["Heat", "Noise", "Water"])
        self.assertEqual(result["share"].tolist(), [0.4, 0.4, 0.2])
        self.assertTrue(rankings(records.iloc[:0], "complaint_type").empty)

    def test_trend_breaks_on_unobserved_dates(self):
        records = pd.DataFrame({"created_date": pd.to_datetime([
            "2025-01-01 01:00", "2025-01-01 03:00", "2025-01-02 01:00", "2025-01-04 01:00",
        ])})
        result = daily_totals(records)
        self.assertEqual(result["requests"].tolist(), [2, 1, 1])
        self.assertEqual(result["segment"].tolist(), [1, 1, 2])

    def test_week_comparison_excludes_edges_and_requires_daily_coverage(self):
        daily = pd.DataFrame({"day": pd.date_range("2025-01-01", periods=16),
                              "requests": [99] + [4] * 7 + [6] * 7 + [99]})
        result = compare_weeks(daily)
        self.assertEqual((result.previous, result.latest, result.change), (28, 42, 0.5))
        self.assertEqual(result.start.date(), date(2025, 1, 2))
        self.assertEqual(result.end.date(), date(2025, 1, 15))
        with_gap = daily.copy()
        with_gap.loc[5, "day"] = pd.Timestamp("2024-12-31")
        self.assertIsNone(compare_weeks(with_gap))
        self.assertIsNone(compare_weeks(daily.assign(requests=1)))

    def test_freshness_distinguishes_missing_stale_and_future(self):
        now = pd.Timestamp("2025-01-15T12:00Z")
        self.assertEqual(freshness(None, now), "Unavailable")
        self.assertEqual(freshness(pd.Timestamp("2025-01-14T12:00Z"), now), "Recent")
        self.assertEqual(freshness(pd.Timestamp("2025-01-14T11:59Z"), now), "Stale")
        self.assertEqual(freshness(pd.Timestamp("2025-01-15T12:01Z"), now), "Check clock")


class DashboardTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.db = Path(directory.name) / "nyc311.sqlite3"

    def test_empty_v2_database_still_shows_pipeline_metrics(self):
        stop = "2025-01-15T12:00:00.000000Z"
        commit_ingestion(self.db, [], None, stop, {"fetched": 0, "accepted": 0, "rejected": 0})
        with patch("nyc311.storage.DEFAULT_DB", self.db):
            app = AppTest.from_file("app.py").run()
            self.assertIn("database is empty", app.info[0].value)
            app.radio[0].set_value("Pipeline health").run()
            self.assertFalse(app.exception)
            self.assertEqual({metric.label: metric.value for metric in app.metric}["Fetched"], "0")

    def test_v2_pipeline_reads_committed_checkpoint_and_stale_source(self):
        stop = "2025-01-15T12:00:00.000000Z"
        modified = "2025-01-15T11:30:00.000000Z"
        rows = [("101", "2025-01-15T09:00:00.000000", "Noise", "QUEENS", modified)]
        commit_ingestion(self.db, iter(rows), None, stop, {"fetched": 2, "accepted": 1, "rejected": 1})
        data = load_dashboard(self.db)
        self.assertEqual(data.source_latest, pd.Timestamp(modified))
        self.assertEqual(data.watermark, pd.Timestamp(stop))
        before = self.db.read_bytes()
        with patch("nyc311.storage.DEFAULT_DB", self.db):
            app = AppTest.from_file("app.py").run()
            app.radio[0].set_value("Pipeline health").run()
            self.assertFalse(app.exception)
            self.assertIn("latest stored source update", app.warning[0].value)
            metrics = {metric.label: metric.value for metric in app.metric}
            self.assertEqual(metrics, {
                "Fetched": "2", "Accepted": "1", "Rejected": "1", "New": "1",
                "Updated": "0", "Unchanged": "0",
            })
        self.assertEqual(self.db.read_bytes(), before)

    def test_v1_can_explore_and_insights_explain_insufficient_history(self):
        save_requests(self.db, clean_requests([request_row()]))
        with patch("nyc311.storage.DEFAULT_DB", self.db):
            app = AppTest.from_file("app.py").run()
            self.assertFalse(app.exception)
            app.radio[0].set_value("Pipeline health").run()
            self.assertIn("No successful V2 ingestion", app.info[0].value)
            app.radio[0].set_value("Insights").run()
            self.assertFalse(app.exception)
            self.assertIn("two consecutive seven-day periods", app.info[0].value)

    def test_invalid_database_shows_actionable_error(self):
        self.db.write_text("not a database")
        with patch("nyc311.storage.DEFAULT_DB", self.db):
            app = AppTest.from_file("app.py").run()
        self.assertFalse(app.exception)
        self.assertIn("readable SQLite file", app.error[0].value)

    def test_invalid_metric_json_keeps_requests_available(self):
        stop = "2025-01-15T12:00:00.000000Z"
        commit_ingestion(self.db, [], None, stop, {"fetched": 0, "accepted": 0, "rejected": 0})
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute("UPDATE ingestion_state SET quality_counts = 'broken'")
        data = load_dashboard(self.db)
        self.assertIsNone(data.counts)
        self.assertEqual(data.watermark, pd.Timestamp(stop))
