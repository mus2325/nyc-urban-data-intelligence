import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests
from streamlit.testing.v1 import AppTest

from nyc311 import etl
from nyc311.extract import API_URL, fetch_requests
from nyc311.storage import load_requests, save_requests
from nyc311.transform import clean_requests


def request_row(key="101", date="2025-01-15T09:00:00.000", category="Noise", borough=" queens "):
    return dict(unique_key=key, created_date=date, complaint_type=category, borough=borough)


class ExtractionTests(unittest.TestCase):
    @patch("nyc311.extract.requests.get")
    def test_fetch_uses_bounded_query_and_timeout(self, get):
        get.return_value.json.return_value = [request_row()]
        self.assertEqual(fetch_requests(3), [request_row()])
        get.return_value.raise_for_status.assert_called_once()
        url, = get.call_args.args
        self.assertEqual(url, API_URL)
        self.assertEqual(get.call_args.kwargs["params"]["$limit"], 3)
        self.assertEqual(get.call_args.kwargs["timeout"], (5, 30))

    @patch("nyc311.extract.requests.get")
    def test_invalid_limit_is_rejected_before_network(self, get):
        with self.assertRaises(ValueError):
            fetch_requests(0)
        get.assert_not_called()

    @patch("nyc311.extract.requests.get")
    def test_unexpected_response_is_rejected(self, get):
        get.return_value.json.return_value = {"error": "invalid query"}
        with self.assertRaisesRegex(ValueError, "response format"):
            fetch_requests(3)


class TransformTests(unittest.TestCase):
    def test_normalizes_and_validates_before_deduplicating(self):
        rows = [
            request_row(date="yesterday"),
            request_row(category=" Noise "),
            request_row(key="00101", category="Duplicate"),
            request_row(key="102", borough=None),
            request_row(key="103", category=" "),
            request_row(key="0"),
            request_row(key="104", date="2025-01-15T09:00:00Z"),
            {},
        ]
        clean = clean_requests(rows)
        self.assertEqual(clean["unique_key"].tolist(), ["101", "102"])
        self.assertEqual(clean["complaint_type"].tolist(), ["Noise", "Noise"])
        self.assertEqual(clean["borough"].tolist(), ["QUEENS", "UNKNOWN"])

    def test_empty_input_is_valid(self):
        self.assertTrue(clean_requests([]).empty)


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.db = Path(directory.name) / "data with spaces" / "nyc311.sqlite3"

    @patch("nyc311.extract.requests.get")
    def test_rerunning_etl_inserts_only_new_ids(self, get):
        get.return_value.json.side_effect = [
            [request_row()],
            [request_row(), request_row(key="102")],
        ]
        self.assertEqual(etl.run(3, self.db), 1)
        self.assertEqual(etl.run(3, self.db), 1)
        self.assertEqual(set(load_requests(self.db)["unique_key"]), {"101", "102"})

    @patch("nyc311.extract.requests.get")
    def test_http_failure_leaves_database_unchanged(self, get):
        save_requests(self.db, clean_requests([request_row()]))
        get.return_value.raise_for_status.side_effect = requests.HTTPError("Unavailable")
        with self.assertRaises(requests.HTTPError):
            etl.run(3, self.db)
        self.assertEqual(load_requests(self.db)["unique_key"].tolist(), ["101"])

    def test_failed_batch_rolls_back(self):
        save_requests(self.db, clean_requests([]))
        records = clean_requests([request_row(), request_row(key="102")])
        records.loc[1, "complaint_type"] = None
        with self.assertRaises(sqlite3.IntegrityError):
            save_requests(self.db, records)
        self.assertTrue(load_requests(self.db).empty)

    def test_dashboard_handles_missing_db_and_filters_loaded_sample(self):
        with patch("nyc311.storage.DEFAULT_DB", self.db):
            app = AppTest.from_file("app.py").run()
            self.assertFalse(app.exception)
            self.assertIn("Load your first sample", app.info[0].value)

            save_requests(self.db, clean_requests([
                request_row(),
                request_row(key="102", date="2025-01-16T10:00:00", category="Heat",
                            borough="BROOKLYN"),
            ]))
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual(app.metric[0].value, "2")
            app.radio[0].set_value("Request explorer").run()
            self.assertFalse(app.exception)
            self.assertEqual(app.metric[0].value, "2")
            self.assertEqual(len(app.get("vega_lite_chart")), 3)
            app.multiselect[0].set_value(["QUEENS"]).run()
            self.assertFalse(app.exception)
            self.assertEqual(app.metric[0].value, "1")
            app.multiselect[1].set_value(["Heat"]).run()
            self.assertFalse(app.exception)
            self.assertEqual(app.metric[0].value, "0")
            self.assertIn("No stored requests", app.info[0].value)
