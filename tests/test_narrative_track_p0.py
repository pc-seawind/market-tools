import narrative_track as nt


def test_p0_research_event_never_tracks():
    e = {"policy_version": "p0-2026-08-01", "pool": "research", "effective_score": 3,
         "alpha_tickers": [{"code": "000001.SZ"}]}
    assert nt._select_tickers_for_verify(e, "light", 14) == ([], set())


def test_p0_trade_uses_alpha_tickers_and_effective_score():
    e = {"policy_version": "p0-2026-08-01", "pool": "trade", "score": 3,
         "effective_score": 2,
         "tickers": [{"code": "SHOULD_NOT_USE"}],
         "alpha_tickers": [{"code": "000001.SZ"}, {"code": "000002.SZ"}, {"code": "000003.SZ"}]}
    selected, days = nt._select_tickers_for_verify(e, "light", 14, score2_max_tickers=2)
    assert [x["code"] for x in selected] == ["000001.SZ", "000002.SZ"]
    assert days == set(nt.LIGHT_VERIFY_MILESTONE_DAYS)


def test_fixed_horizon_breakdown_does_not_use_latest():
    by_pair = {
        ("a", "x"): {"all": [{"days_since_event": 14, "event_score": 3, "hit": True,
            "excess_pct": 1.0, "hit_strict": True}]},
        ("b", "y"): {"all": [{"days_since_event": 14, "event_score": 3, "hit": False,
            "excess_pct": -2.0, "hit_strict": False}]},
    }
    d = nt._fixed_horizon_breakdown(by_pair, "event_score", 14)["3"]
    assert d["samples"] == 2
    assert d["hit_rate"] == 50.0
