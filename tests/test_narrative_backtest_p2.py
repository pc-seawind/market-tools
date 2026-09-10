import json
import narrative_backtest_p2 as p2


def test_cache_key_matches_tushare_shape():
    assert p2._cache_key("daily", {"trade_date": "20260608"}) == "0ff54866cea9a4c7ac7fcc887d4259cc"


def test_member_returns_are_point_to_point():
    start = {"A": {"close": 10.0}, "B": {"close": 20.0}}
    end = {"A": {"close": 11.0}, "B": {"close": 18.0}}
    assert [round(x, 6) for x in p2._member_returns(["A", "B"], start, end)] == [10.0, -10.0]


def test_confirmation_recomputes_shifted_entry(monkeypatch):
    snapshots = {
        "20260102": {
            "A": {"open": 10.0, "high": 11.2, "low": 9.8, "close": 11.0,
                  "pre_close": 10.0, "pct_chg": 10.0},
            "B": {"close": 10.1, "pct_chg": 1.0},
        },
        "20260116": {"A": {"close": 12.1}, "B": {"close": 10.2}},
    }
    monkeypatch.setattr(p2, "daily_snapshot", lambda d: snapshots[d])
    monkeypatch.setattr(p2, "on_or_before", lambda d: d)
    monkeypatch.setattr(p2, "industry_members", lambda c: ("test", ["A", "B"]))
    monkeypatch.setattr(p2, "index_bar", lambda c, d: {"close": 100.0 if d == "20260102" else 105.0})
    monkeypatch.setattr(p2, "sector_return", lambda c, a, b: 2.0)
    row = {"code": "A", "baseline_date": "20260102", "verify_date": "20260116", "side": "+"}
    out = p2.confirmation(row)
    assert out["confirm_pass"] is True
    # Entry must be the observed close (11), not the original open (10).
    assert round(out["confirm_stock_return"], 6) == 10.0
    assert round(out["confirm_signed_excess"], 6) == 5.0
    assert round(out["confirm_signed_sector"], 6) == 8.0


def test_p2_filters_increment_from_p1_rank1(monkeypatch):
    monkeypatch.setattr(p2.p1, "filt", lambda r, kind: bool(r.get(kind)))
    row = {
        "p1": True, "mapping": True, "ticker_rank": 1,
        "sector_position_120d": 70.0, "sector_ret_20": 5.0,
        "sector_breadth_20": 60.0, "pre_volatility_20": 3.5,
        "pre_atr_pct_20": 4.5, "pre_max_drawdown_60": -20.0,
        "confirm_pass": True,
    }
    assert p2.base_filter(row, "p1_rank1")
    assert p2.base_filter(row, "p2_sector")
    assert p2.base_filter(row, "p2_volatility")
    assert p2.base_filter(row, "p2_risk")
    assert p2.base_filter(row, "p2_confirm")
    row["ticker_rank"] = 2
    assert not p2.base_filter(row, "p2_risk")
