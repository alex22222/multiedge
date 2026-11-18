"""Versioned retest plans keep the initial and later structure protection aligned."""
import copy

import pandas as pd
import pytest

from multiedge.structure_v2 import (POLICY_VERSION, build_entry_plan, build_stop_update,
                                   entry_price_check)


def prices():
    index = pd.bdate_range(end="2026-11-17", periods=60)
    close = pd.Series([100.] * 60, index=index)
    frame = pd.DataFrame({"Open": close - .1, "High": close + 1,
                          "Low": close - 1, "Close": close, "Volume": 1000})
    frame.iloc[-3, frame.columns.get_loc("Low")] = 98.5
    return frame


def report(**values):
    return {"ticker": "ABC", "as_of": "2026-11-17", "verdict": "BUY", "close": 100,
            "pullback_age": 5, "structural_mature": ["ma_zone", "sr_flip"],
            "edges": {"ma_zone": {"level": 99.5}, "sr_flip": {"level": 99}},
            "stop_anchor": 70, "stop": 69, "target": 112, **values}


def plan(frame=None, source=None, **values):
    return build_entry_plan(source or report(), frame if frame is not None else prices(),
                            source_as_of="2026-11-17", observed_at="2026-11-17T23:30:00+00:00", **values)


def test_initial_stop_uses_this_pullback_not_distant_old_e1_and_freezes_evidence():
    frame = prices()
    frame.attrs["snapshots"] = [{"raw_sha256": "source-one"}]
    saved = frame.copy(deep=True)
    result = plan(frame, source_run_id="run-1")
    risk = result["risk_plan"]
    assert result["eligible"] is True and result["reason"] == "entry_plan_ready"
    assert result["stop"] == 98.29 and result["stop"] != 69
    assert risk["strategy_version"] == POLICY_VERSION
    assert risk["anchor_date"] == frame.index[-3].date().isoformat()
    assert risk["anchor_price"] == 98.5 and risk["support_band"] == [99, 99.5]
    assert risk["minimum_net_rr"] == 3 and risk["reference_net_rr"] > 3
    assert risk["source_run_id"] == "run-1" and len(risk["source_fingerprint"]) == 64
    assert risk["source_snapshots"] == frame.attrs["snapshots"]
    pd.testing.assert_frame_equal(frame, saved)


def test_google_frozen_case_keeps_its_structure_level_and_has_a_net_three_r_plan():
    from multiedge.engine import analyze
    from multiedge.research import load_prices
    frame = load_prices("GOOGL", "2025-08-01", "data")
    spx = load_prices("SPX", "2025-08-01", "data")
    source = analyze(frame, spx, "GOOGL", "2025-08-01").to_dict()
    result = build_entry_plan(source, frame, source_as_of="2025-08-01",
                              observed_at="2025-08-01T23:00:00+00:00")
    assert result["eligible"] and result["stop"] == 184.91 and result["target"] == 207.05
    assert result["risk_plan"]["reference_net_rr"] == pytest.approx(3.8144684103)


@pytest.mark.parametrize("target,reason", [(None, "missing_target"), (99, "invalid_target"), (102, "reference_rr_below_minimum")])
def test_target_and_reference_net_reward_risk_are_required(target, reason):
    result = plan(source=report(target=target))
    assert not result["eligible"] and result["reason"] == reason
    assert result["risk_plan"]["reason"] == reason


def test_buy_state_without_actual_pullback_or_confluence_is_not_executable():
    assert plan(source=report(verdict="WATCH"))["reason"] == "signal_not_buy"
    assert plan(source=report(pullback_age=0))["reason"] == "missing_pullback"
    assert plan(source=report(structural_mature=["ma_zone"]))["reason"] == "insufficient_confluence"
    source = report()
    source["edges"]["sr_flip"]["level"] = 80
    assert plan(source=source)["reason"] == "support_band_too_wide"
    frame = prices()
    frame.iloc[-3, frame.columns.get_loc("Low")] = 70
    assert plan(frame)["reason"] == "anchor_outside_support"


def test_initial_structure_must_include_ma_support_used_by_future_trailing():
    source = report(structural_mature=["sr_flip", "trendline"])
    source["edges"]["trendline"] = {"level": 99.5}
    result = plan(source=source)
    assert not result["eligible"] and result["reason"] == "missing_ma_confluence"
    accepted = plan()
    assert accepted["risk_plan"]["required_support"] == "ma_zone_and_structure"
    assert accepted["risk_plan"]["ma_zone_level"] == 99.5


def test_source_close_must_reclaim_support_without_chasing_far_above_it():
    assert plan(source=report(close=98))["reason"] == "source_below_support"
    assert plan(source=report(close=110))["reason"] == "source_above_entry_limit"


def test_actual_entry_rechecks_net_rr_and_price_instead_of_trusting_signal_close():
    risk = plan()["risk_plan"]
    accepted = entry_price_check(risk, 100, fee_bps=5, slippage_bps=5)
    assert accepted["eligible"] and accepted["entry_price"] == pytest.approx(100.05)
    assert accepted["net_risk_per_share"] == pytest.approx(100.05 * 1.0005 - 98.29 * .9995 * .9995)
    assert accepted["net_reward_per_share"] == pytest.approx(112 * .9995 * .9995 - 100.05 * 1.0005)
    assert accepted["net_rr"] == pytest.approx(accepted["net_reward_per_share"] / accepted["net_risk_per_share"])
    assert not entry_price_check(risk, 110, fee_bps=5, slippage_bps=5)["eligible"]
    assert entry_price_check(risk, 98.5, fee_bps=5, slippage_bps=5)["reason"] == "entry_below_support"
    tight = {**risk, "target": 105.3, "maximum_entry_price": 110}
    assert entry_price_check(tight, 100, fee_bps=0, slippage_bps=0)["eligible"]
    assert entry_price_check(tight, 100, fee_bps=50, slippage_bps=50)["reason"] == "entry_rr_below_minimum"


def test_entry_below_stop_target_gap_or_ineligible_plan_is_rejected():
    risk = plan()["risk_plan"]
    assert entry_price_check(risk, 98.5, fee_bps=0, slippage_bps=0)["reason"] == "entry_below_support"
    assert entry_price_check(risk, 97, fee_bps=0, slippage_bps=0)["reason"] == "invalid_stop"
    assert entry_price_check({**risk, "maximum_entry_price": 200}, 112, fee_bps=0, slippage_bps=0)["reason"] == "target_at_or_below_entry"
    assert entry_price_check({**risk, "eligible": False, "reason": "missing_target"}, 100, fee_bps=0, slippage_bps=0)["reason"] == "missing_target"


def test_buy_slippage_cannot_turn_an_open_below_support_into_a_valid_reclaim():
    risk = plan()["risk_plan"]
    checked = entry_price_check(risk, 98.99, fee_bps=5, slippage_bps=5)
    assert checked["entry_price"] > risk["support_band"][0]
    assert not checked["eligible"] and checked["reason"] == "entry_below_support"


def test_future_rows_do_not_change_plan_or_identity():
    frame = prices()
    original = plan(frame)
    frame.loc[pd.Timestamp("2026-11-18")] = [float("nan"), -10, -20, -30, -1]
    assert plan(frame) == original


@pytest.mark.parametrize("mutation", ["duplicate", "missing_day", "nan"])
def test_bad_completed_price_history_is_rejected(mutation):
    frame = prices()
    if mutation == "duplicate": frame = pd.concat([frame, frame.iloc[-1:]])
    if mutation == "missing_day": frame = frame.iloc[:-1]
    if mutation == "nan": frame.iloc[-1, frame.columns.get_loc("Low")] = float("nan")
    with pytest.raises(ValueError): plan(frame)


def test_incomplete_or_wrong_signal_day_cannot_generate_a_plan():
    with pytest.raises(ValueError):
        build_entry_plan(report(), prices(), source_as_of="2026-11-17", observed_at="2026-11-17T21:29:59+00:00")
    with pytest.raises(ValueError): plan(source=report(as_of="2026-11-16"))


def holding(frame, **values):
    return {"ticker": "ABC", "source_signal_id": "signal-1", "entry_date": frame.index[45].date().isoformat(),
            "entry_timestamp": frame.index[45].date().isoformat() + "T15:00:00+00:00", "entry_price": 100,
            "stop": 90, "risk_plan": {"strategy_version": POLICY_VERSION}, **values}


def higher_low_prices():
    frame = prices()
    frame.loc[:, "Open"] = 103.0
    frame.loc[:, "High"] = 104.0
    frame.loc[:, "Low"] = 101.0
    frame.loc[:, "Close"] = 103.0
    frame.iloc[40, frame.columns.get_loc("Low")] = 95
    frame.iloc[53, frame.columns.get_loc("Low")] = 102
    # Surround the new higher low with strictly greater lows.
    for i in [50, 51, 52, 54, 55, 56]:
        frame.iloc[i, frame.columns.get_loc("Low")] = 102.5
    return frame


def update(frame=None, position=None, **values):
    frame = frame if frame is not None else higher_low_prices()
    return build_stop_update(position or holding(frame), frame, source_as_of=values.pop("source_as_of", "2026-11-17"),
                             observed_at=values.pop("observed_at", "2026-11-17T23:30:00+00:00"),
                             enabled_at="2026-10-01T00:00:00+00:00", **values)


def test_trailing_requires_post_entry_confirmed_higher_low_and_ma_confluence():
    frame = higher_low_prices()
    result = update(frame)
    assert result["policy_version"] == POLICY_VERSION
    assert result["new_stop"] > 90 and result["new_stop"] < 103
    assert result["reason"] == "confluent_higher_low_confirmed"
    assert result["evidence"]["selected_basis"] == "confluent_higher_low"
    assert result["evidence"]["pivot_date"] == frame.index[53].date().isoformat()
    assert result["new_stop"] == result["evidence"]["structure_candidate"]
    assert update(frame, holding(frame, stop=103))["new_stop"] == 103


def test_isolated_ma_and_old_pivots_never_replace_initial_structure():
    frame = prices()
    assert update(frame, holding(frame))["new_stop"] == 90
    frame = higher_low_prices()
    assert update(frame, holding(frame, entry_date="2026-11-17"))["new_stop"] == 90
    frame.iloc[53, frame.columns.get_loc("Low")] = 99
    assert update(frame)["new_stop"] == 90


def test_higher_low_needs_three_completed_right_hand_sessions():
    frame = higher_low_prices()
    source = frame.index[55].date().isoformat()
    assert update(frame, source_as_of=source, observed_at=source + "T23:30:00+00:00")["new_stop"] == 90
    source = frame.index[56].date().isoformat()
    assert update(frame, source_as_of=source, observed_at=source + "T23:30:00+00:00")["new_stop"] > 90


def test_existing_legacy_holdings_cannot_receive_v2_proposals_and_splits_pause():
    frame = higher_low_prices()
    with pytest.raises(ValueError, match="version"):
        update(frame, holding(frame, risk_plan={"basis": "old"}))
    frame.attrs["splits"] = [{"date": "2026-11-17"}]
    with pytest.raises(ValueError, match="split"): update(frame)


def test_update_is_pure_has_stable_identity_and_does_not_use_future_rows():
    frame = higher_low_prices()
    original = copy.deepcopy(holding(frame))
    first = update(frame, original)
    later = update(frame, original, observed_at="2026-11-18T00:00:00+00:00")
    assert later["update_id"] == first["update_id"]
    frame.loc[pd.Timestamp("2026-11-18")] = [-1, -2, -3, float("nan"), -1]
    assert update(frame, original) == first
    assert original == holding(higher_low_prices())
