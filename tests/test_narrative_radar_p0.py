import datetime as dt
import narrative_radar as nr


def universe():
    return {
        "late_stage_subdomains": ["late"],
        "event_type_patterns": {
            "confirmed_order": {"keywords": ["中标"], "score_penalty": 0},
            "capacity_plan": {"keywords": ["拟投资"], "score_penalty": 1},
        },
    }


def base(**overrides):
    event = {
        "ts": "2026-08-01T19:30:00+08:00",
        "subdomain": "normal",
        "score": 3,
        "title": "公司中标10亿元采购订单",
        "published_at": "2026-08-01T18:00:00+08:00",
        "pub_date": "20260801",
        "session": "post",
        "source_tier": "primary",
        "surprise": "high",
        "mapping_evidence": "公告确认客户、金额和交付期",
        "tradeability_features": {"position_120d": 70.0, "pre_ret_20": 12.0, "volume_ratio_5_20": 1.2},
        "alpha_tickers": [{"code": "000001.SZ", "ticker_layer": "core_pure"}],
    }
    event.update(overrides)
    event.update(nr.compute_event_quality(event, universe()))
    return event


def test_classifier_uses_raw_title_only():
    et, penalty = nr.classify_event_type("普通更新", rationale="拟投资扩产", universe=universe())
    assert (et, penalty) == ("other", 0)


def test_capacity_plan_gets_penalty():
    q = nr.compute_event_quality(base(title="公司拟投资建设新产能", score=2), universe())
    assert q["event_type"] == "capacity_plan"
    assert q["effective_score"] == 1


def test_trade_eligible_happy_path():
    assert nr.assess_trade_eligibility(base(), []) == []


def test_late_stage_and_missing_fields_are_blocked():
    e = base(subdomain="late", published_at="", pub_date="", source_tier="unknown")
    reasons = nr.assess_trade_eligibility(e, [])
    assert "late_stage_blocked" in reasons
    assert "missing_published_at" in reasons
    assert "source_not_primary_or_industry" in reasons


def test_cooldown_only_uses_prior_trade_pool():
    e = base()
    prior = {**e, "ts": "2026-07-25T19:30:00+08:00", "pool": "trade"}
    reasons = nr.assess_trade_eligibility(e, [prior])
    assert any(x.startswith("cooldown_14d:") for x in reasons)
    prior["pool"] = "research"
    assert not any(x.startswith("cooldown_14d:") for x in nr.assess_trade_eligibility(e, [prior]))


def test_parse_published_at_normalizes_to_cst():
    parsed, day = nr._parse_published_at("2026-08-01T08:00:00Z")
    assert parsed.utcoffset() == dt.timedelta(hours=8)
    assert day == "20260801"


def test_missing_price_crowding_features_are_blocked():
    e = base(tradeability_features={})
    reasons = nr.assess_trade_eligibility(e, [])
    assert "missing_price_crowding_features" in reasons


def test_p1_price_crowding_happy_path():
    e = base(tradeability_features={
        "position_120d": 70.0,
        "pre_ret_20": 12.0,
        "volume_ratio_5_20": 1.2,
    })
    assert nr.assess_trade_eligibility(e, []) == []


def test_p1_price_crowding_thresholds_block():
    e = base(tradeability_features={
        "position_120d": 81.0,
        "pre_ret_20": 26.0,
        "volume_ratio_5_20": 1.9,
    })
    reasons = nr.assess_trade_eligibility(e, [])
    assert "position_120d_above_80" in reasons
    assert "pre_ret_20_above_25" in reasons
    assert "volume_ratio_5_20_above_1.8" in reasons
