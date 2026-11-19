import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from nyc311.etl import run_incremental
from nyc311.extract import utc_text
from nyc311.storage import load_requests, read_checkpoint, save_requests
from nyc311.transform import clean_requests
from tests.test_pipeline import request_row

NOW = datetime.fromisoformat("2025-01-15T12:00:00Z")


def change(key="101", timestamp="2025-01-15T11:55:00.000Z", **fields):
    return {**request_row(key, **fields), "source_updated_at": timestamp}


def response(rows):
    result = Mock()
    result.json.return_value = rows
    return result


class IncrementalTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.db = Path(folder.name) / "nyc311.sqlite3"

    def state(self):
        with closing(sqlite3.connect(self.db)) as connection:
            return connection.execute(
                "SELECT watermark, last_successful_run, quality_counts FROM ingestion_state"
            ).fetchone()

    @patch("nyc311.extract.requests.get")
    def test_migrates_v1_and_inserts_updates_and_replays(self, get):
        save_requests(self.db, clean_requests([request_row(), request_row("103")]))
        rows = [change(category="Heat"), change("102")]
        get.side_effect = [response(rows), response(rows)]

        first = run_incremental(5, self.db, now=NOW)
        self.assertEqual((first["new"], first["updated"], first["unchanged"]), (1, 1, 0))
        self.assertEqual(first["fetched"], 2)
        self.assertEqual(first["accepted"], 2)
        self.assertEqual(first["rejected"], 0)
        self.assertEqual(
            load_requests(self.db).set_index("unique_key")["complaint_type"].to_dict(),
            {"101": "Heat", "102": "Noise", "103": "Noise"},
        )

        next_run = NOW + timedelta(minutes=1)
        second = run_incremental(5, self.db, now=next_run)
        self.assertEqual((second["new"], second["updated"], second["unchanged"]), (0, 0, 2))
        where = get.call_args.kwargs["params"]["$where"]
        self.assertIn("2025-01-15T11:50:00.000000Z", where)
        checkpoint, last_run, counts = self.state()
        self.assertEqual(checkpoint, utc_text(next_run))
        self.assertEqual(last_run, second["last_successful_run"])
        self.assertEqual(json.loads(counts), second)

    @patch("nyc311.extract.requests.get")
    def test_pages_use_numeric_tie_breaker_and_same_upper_boundary(self, get):
        get.side_effect = [response([change("9"), change("10")]), response([change("11")])]
        counts = run_incremental(2, self.db, now=NOW)
        self.assertEqual(counts["new"], 3)

        first, second = [call.kwargs["params"] for call in get.call_args_list]
        self.assertIn(":updated_at AS source_updated_at", first["$select"])
        self.assertEqual(first["$order"], ":updated_at ASC, unique_key::number ASC")
        self.assertEqual(first["$limit"], 2)
        self.assertIn(utc_text(NOW), first["$where"])
        self.assertTrue(second["$where"].startswith(first["$where"]))
        self.assertIn("unique_key::number > 10", second["$where"])
        self.assertNotIn("$offset", second)

    @patch("nyc311.extract.requests.get")
    def test_empty_result_advances_scanned_window(self, get):
        get.return_value = response([])
        first = run_incremental(5, self.db, now=NOW)
        self.assertEqual(first["fetched"], 0)
        self.assertEqual(read_checkpoint(self.db), utc_text(NOW))
        self.assertTrue(load_requests(self.db).empty)

    @patch("nyc311.extract.requests.get")
    def test_explicit_start_bounds_catchup_and_cannot_replace_existing_checkpoint(self, get):
        get.return_value = response([])
        start = NOW - timedelta(days=3)
        run_incremental(5, self.db, now=NOW, since=start)
        self.assertEqual(read_checkpoint(self.db), utc_text(start + timedelta(days=1)))
        with self.assertRaisesRegex(ValueError, "no checkpoint"):
            run_incremental(5, self.db, now=NOW, since=start)
        get.assert_called_once()

    @patch("nyc311.extract.requests.get")
    def test_tracks_rejected_rows_and_keeps_newest_version(self, get):
        get.return_value = response([
            change(),
            change("102", category=" "),
            change(timestamp="2025-01-15T11:58:00Z", category="Heat"),
        ])
        counts = run_incremental(5, self.db, now=NOW)
        self.assertEqual((counts["fetched"], counts["accepted"], counts["rejected"]), (3, 1, 2))
        self.assertEqual(load_requests(self.db)["complaint_type"].tolist(), ["Heat"])

    @patch("nyc311.extract.time.sleep")
    @patch("nyc311.extract.requests.get")
    def test_retries_transient_failures_and_honors_retry_after(self, get, sleep):
        throttled = requests.Response()
        throttled.status_code = 429
        throttled.headers["Retry-After"] = "3"
        get.side_effect = [
            requests.Timeout("Timed out"),
            requests.HTTPError("Rate limited", response=throttled),
            response([change()]),
        ]
        self.assertEqual(run_incremental(5, self.db, now=NOW)["new"], 1)
        self.assertEqual([call.args for call in sleep.call_args_list], [(1,), (3,)])
        self.assertEqual(get.call_args_list[0], get.call_args_list[2])

    @patch("nyc311.extract.time.sleep")
    @patch("nyc311.extract.requests.get")
    def test_later_page_failure_leaves_rows_and_checkpoint_unchanged(self, get, _sleep):
        get.return_value = response([change()])
        run_incremental(5, self.db, now=NOW)
        before = self.state()

        get.side_effect = [response([change("102")]), *[requests.Timeout("Timed out")] * 3]
        next_run = NOW + timedelta(minutes=1)
        with self.assertRaises(requests.Timeout):
            run_incremental(1, self.db, now=next_run)
        self.assertEqual(self.state(), before)
        self.assertEqual(load_requests(self.db)["unique_key"].tolist(), ["101"])

    @patch("nyc311.extract.requests.get")
    def test_missing_source_timestamp_fails_without_checkpoint(self, get):
        get.return_value = response([request_row()])
        with self.assertRaisesRegex(ValueError, "source_updated_at"):
            run_incremental(5, self.db, now=NOW)
        self.assertFalse(self.db.exists())

    @patch("nyc311.extract.requests.get")
    def test_database_failure_rolls_back_prior_rows_and_checkpoint(self, get):
        get.return_value = response([change()])
        run_incremental(5, self.db, now=NOW)
        before = self.state()
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute("""
                CREATE TRIGGER reject_row BEFORE INSERT ON requests
                WHEN NEW.unique_key = '103'
                BEGIN
                    SELECT RAISE(ABORT, 'test write failure');
                END
            """)
        get.return_value = response([change("103"), change("102", timestamp="2025-01-15T11:56:00Z")])
        with self.assertRaisesRegex(sqlite3.IntegrityError, "test write failure"):
            run_incremental(5, self.db, now=NOW + timedelta(minutes=1))
        self.assertEqual(self.state(), before)
        self.assertEqual(load_requests(self.db)["unique_key"].tolist(), ["101"])
