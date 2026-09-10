import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import narrative_track as nt


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_prewarm_verify_state_reuses_stock_and_benchmark_baselines(tmp_path, monkeypatch):
    perf_path = tmp_path / "perf.jsonl"
    _write_jsonl(
        perf_path,
        [
            {
                "event_ts": "event-1",
                "code": "000001.SZ",
                "days_since_event": 1,
                "baseline_date": "20260701",
                "baseline_price": 10.5,
                "benchmark": "000300.SH",
                "benchmark_baseline": 4000.0,
            },
            # A malformed historical row must not poison baseline caches.
            {"event_ts": "event-2", "code": "000002.SZ", "days_since_event": 2},
        ],
    )
    monkeypatch.setattr(nt, "_PERF_PATH", perf_path)

    seen, cache = nt._prewarm_verify_state(
        [{"ts": "event-1", "session": "post"}, {"ts": "event-2"}]
    )

    assert ("event-1", "000001.SZ", 1) in seen
    assert ("event-2", "000002.SZ", 2) in seen
    assert cache[("event-1", "000001.SZ")] == ("20260701", 10.5)
    assert cache[("bench", "000300.SH", "20260701", "post")] == 4000.0
    assert ("event-2", "000002.SZ") not in cache


def test_verify_all_does_not_resolve_existing_pair_baseline(tmp_path, monkeypatch):
    events_path = tmp_path / "events.jsonl"
    perf_path = tmp_path / "perf.jsonl"
    event = {
        "ts": "event-1",
        "trade_date": "20260630",
        "pub_date": "20260630",
        "session": "post",
        "score": 3,
        "tickers": [{"code": "000001.SZ", "name": "平安银行", "side": "+"}],
    }
    _write_jsonl(events_path, [event])
    _write_jsonl(
        perf_path,
        [
            {
                "event_ts": "event-1",
                "code": "000001.SZ",
                "days_since_event": 1,
                "baseline_date": "20260701",
                "baseline_price": 10.5,
                "benchmark": "000300.SH",
                "benchmark_baseline": 4000.0,
            }
        ],
    )
    monkeypatch.setattr(nt, "_EVENTS_PATH", events_path)
    monkeypatch.setattr(nt, "_PERF_PATH", perf_path)

    def fail_resolve(*args, **kwargs):
        raise AssertionError("resolve_base must not run when perf history already stores the baseline")

    monkeypatch.setattr(nt, "resolve_base", fail_resolve)

    calls = []

    def fake_verify(event_arg, ticker_arg, verify_date=None, cached_baseline=None, seen_keys=None):
        calls.append((event_arg, ticker_arg, verify_date))
        assert cached_baseline[("event-1", "000001.SZ")] == ("20260701", 10.5)
        return {"ok": True}

    monkeypatch.setattr(nt, "verify_event_ticker", fake_verify)

    stats = nt.verify_all(verify_date="20260710", mode="light")

    assert len(calls) == 1
    assert stats["verified"] == 1
    assert stats["fetch_failed"] == 0


def test_fetch_stock_close_reuses_a_share_snapshot(monkeypatch):
    import narrative_sector_bench as sector

    monkeypatch.setattr(
        sector, "_fetch_daily_snapshot", lambda trade_date: {"000001.SZ": 12.34}
    )

    def fail_ts_csv(*args, **kwargs):
        raise AssertionError("per-ticker tushare fetch must not run on a snapshot hit")

    monkeypatch.setattr(nt, "_ts_csv", fail_ts_csv)
    assert nt._fetch_stock_close("000001.SZ", "20260730") == 12.34


def test_verify_all_skips_unsupported_us_without_fetch_failure(tmp_path, monkeypatch):
    events_path = tmp_path / "events.jsonl"
    perf_path = tmp_path / "perf.jsonl"
    _write_jsonl(
        events_path,
        [{
            "ts": "us-event",
            "trade_date": "20260701",
            "pub_date": "20260701",
            "session": "post",
            "score": 3,
            "tickers": [{"code": "IBM.US", "name": "IBM", "side": "+"}],
        }],
    )
    _write_jsonl(perf_path, [])
    monkeypatch.setattr(nt, "_EVENTS_PATH", events_path)
    monkeypatch.setattr(nt, "_PERF_PATH", perf_path)

    def fail_resolve(*args, **kwargs):
        raise AssertionError("unsupported markets must be filtered before baseline resolution")

    monkeypatch.setattr(nt, "resolve_base", fail_resolve)
    stats = nt.verify_all(verify_date="20260710", mode="light")

    assert stats["skipped_unsupported"] == 1
    assert stats["fetch_failed"] == 0
    assert stats["verified"] == 0


def test_verify_event_ticker_rejects_unsupported_market_before_io(monkeypatch):
    def fail_io(*args, **kwargs):
        raise AssertionError("unsupported market must not perform market-data I/O")

    monkeypatch.setattr(nt, "resolve_base", fail_io)
    result = nt.verify_event_ticker(
        {"ts": "us-event", "trade_date": "20260701", "score": 3},
        {"code": "IBM.US", "name": "IBM", "side": "+"},
        verify_date="20260710",
    )
    assert result is None
