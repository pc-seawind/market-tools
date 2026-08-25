import contextlib
import datetime as dt
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

import thesis_enrich_daily as ted


def sample_thesis():
    return {
        "ticker": "000001.SZ",
        "name": "测试公司",
        "market": "CN",
        "status": "ACTIVE",
        "pillars": [{"name": "收入增长"}],
        "stop_loss": {},
        "update_log": [],
    }


def sample_metrics():
    return {
        "price": 10.0,
        "r1d": 1.0,
        "r1w": 2.0,
        "r1m": 3.0,
        "r3m": 4.0,
        "position": 50.0,
        "vol_ratio": 1.0,
        "high_120": 12.0,
        "low_120": 8.0,
    }


def write_thesis(path: Path, update_log=None):
    data = sample_thesis()
    data["update_log"] = update_log or []
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))


class ThesisEnrichDailyTests(unittest.TestCase):
    def test_has_update_for_date_handles_yaml_date_objects(self):
        thesis = {"update_log": [{"date": dt.date(2026, 8, 25)}]}
        self.assertTrue(ted.has_update_for_date(thesis, "2026-08-25"))
        self.assertFalse(ted.has_update_for_date(thesis, "2026-08-26"))

    def test_append_update_is_atomic_and_valid(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "000001.SZ.yaml"
            write_thesis(target)
            before_mode = target.stat().st_mode
            entry = ted.build_update_entry(
                "2026-08-25", sample_thesis(), sample_metrics(), [], None, None,
                "price_trigger: 未触发", "unit-test",
            )

            ted.append_update_to_yaml(target, entry)

            parsed = yaml.safe_load(target.read_text())
            self.assertEqual(str(parsed["update_log"][-1]["date"]), "2026-08-25")
            self.assertEqual(parsed["update_log"][-1]["technical_status"]["signal"], "CLEAN")
            self.assertEqual(target.stat().st_mode, before_mode)
            self.assertEqual(list(Path(d).glob(".000001.SZ.yaml.*.tmp")), [])

    def test_append_validation_failure_preserves_original(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "000001.SZ.yaml"
            write_thesis(target)
            original = target.read_text()
            with mock.patch.object(ted, "_validate_yaml_text", return_value=(False, "bad yaml")):
                with self.assertRaisesRegex(ValueError, "原文件未改动"):
                    ted.append_update_to_yaml(
                        target,
                        {"date": "2026-08-25", "data_point": "x", "source": "test"},
                    )
            self.assertEqual(target.read_text(), original)

    def test_main_retry_skips_existing_date_without_fetch(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "000001.SZ.yaml"
            write_thesis(target, [{"date": "2026-08-25", "technical_status": {"signal": "CLEAN"}}])
            result_path = Path("/tmp/thesis_enrich_2026-08-25.json")
            try:
                with mock.patch.object(ted, "fetch_daily_bars", side_effect=AssertionError("must skip")):
                    with mock.patch.object(sys, "argv", [
                        "thesis_enrich_daily.py", "--thesis-dir", d, "--date", "2026-08-25"
                    ]):
                        with contextlib.redirect_stdout(io.StringIO()):
                            ted.main()
                result = json.loads(result_path.read_text())
                self.assertEqual(result["updated"], 0)
                self.assertEqual(result["skipped_existing"], 1)
                self.assertEqual(result["failed"], [])
            finally:
                result_path.unlink(missing_ok=True)

    def test_main_does_not_write_when_history_is_insufficient(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "000001.SZ.yaml"
            write_thesis(target)
            original = target.read_text()
            result_path = Path("/tmp/thesis_enrich_2026-08-26.json")
            try:
                with mock.patch.object(ted, "fetch_daily_bars", return_value=[]):
                    with mock.patch.object(sys, "argv", [
                        "thesis_enrich_daily.py", "--thesis-dir", d, "--date", "2026-08-26"
                    ]):
                        with contextlib.redirect_stdout(io.StringIO()):
                            ted.main()
                result = json.loads(result_path.read_text())
                self.assertEqual(result["updated"], 0)
                self.assertEqual(len(result["failed"]), 1)
                self.assertIn("日线不足 20 条", result["failed"][0]["error"])
                self.assertEqual(target.read_text(), original)
            finally:
                result_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
