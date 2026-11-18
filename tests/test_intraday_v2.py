"""V2 rechecks retest and net RR before a causally later simulated fill."""
import copy

import pytest

from multiedge.intraday import apply_intraday
from tests.test_intraday import bar, signal
from tests.test_intraday_structural import STRUCTURAL


VERSION = "structural_retest_v2"
CONFIG = {**STRUCTURAL, "strategy_version": VERSION}
START = "2026-11-17T14:32:00+00:00"
POLICY = {"version": VERSION, "enabled_at": START}
PLAN = {"eligible": True, "reason": "eligible", "strategy_version": VERSION,
        "support_band": [99, 100], "anchor_date": "2026-11-13", "anchor_price": 97.2,
        "stop": 97, "target": 120, "atr": 2, "buffer_value": .2,
        "minimum_net_rr": 3, "reference_net_rr": 6,
        "maximum_entry_price": 102, "confirmation_close": 100, "retest_ceiling": 100.2}


def candidate(**values):
    return signal(stop=97, target=120, risk_plan=copy.deepcopy(PLAN), **values)


def quotes(*values):
    return {"SPX": [bar(day="2026-11-16", minute="20:55"), *values], "ABC": list(values)}


def apply(*values, signals=None, state=None, now="2026-11-17T14:40:10+00:00", config=CONFIG, policy=POLICY):
    return apply_intraday([candidate()] if signals is None else signals, quotes(*values) if values else {},
                          now=now, config=config, state=state, started_at=START,
                          trailing_policy=policy)


def retest(minute="14:35", **values):
    return bar(minute=minute, o=99.5, h=101, low=99, close=100.5, **values)


def confirmed():
    return apply(retest())


def filled(config=CONFIG):
    first = apply(retest(), config=config)
    return apply(bar(minute="14:40", o=100.5, h=102, low=100, close=101),
                 bar(minute="14:45", o=101, h=102, low=100, close=101.5),
                 state=first, signals=[], now="2026-11-17T14:50:10+00:00", config=config)


def test_retest_confirmation_does_not_fill_at_the_same_bar_open():
    first = confirmed()
    assert first["positions"] == []
    order = first["orders"][0]
    assert order["status"] == "pending" and order["reason"] == "retest_confirmed"
    assert order["entry_confirmation"]["observed_at"] == "2026-11-17T14:40:10+00:00"
    assert order["entry_confirmation"]["price_bar"]["end"] == "2026-11-17T14:40:00+00:00"


def test_actual_observation_prevents_filling_a_bar_that_already_opened():
    first = confirmed()
    second = apply(bar(minute="14:40", o=100.5, h=102, low=100, close=101),
                   signals=[], state=first, now="2026-11-17T14:45:10+00:00")
    assert second["positions"] == []
    assert second["orders"][0]["status"] == "pending"
    third = apply(bar(minute="14:45", o=101, h=102, low=100, close=101.5),
                  signals=[], state=second, now="2026-11-17T14:50:10+00:00")
    position = third["positions"][0]
    assert position["entry_timestamp"] == "2026-11-17T14:45:00+00:00"
    assert position["entry_checks"]["status"] == "passed"
    assert position["entry_checks"]["net_rr"] >= 3
    assert position["entry_checks"]["confirmation_observed_at"] < position["entry_timestamp"]
    assert position["initial_stop"] == 97 and position["initial_risk_per_share"] == 4
    assert third["strategy_version"] == VERSION and third["entry_policy"]


def test_one_delayed_batch_cannot_retroactively_confirm_and_buy():
    result = apply(retest(), retest(minute="14:40"), now="2026-11-17T14:45:10+00:00")
    assert result["positions"] == []
    assert result["orders"][0]["entry_confirmation"]["price_bar"]["timestamp"] == "2026-11-17T14:40:00+00:00"


def test_retest_requires_touch_and_bullish_reclaim():
    non_touch = apply(bar(o=101, h=103, low=100.5, close=102))
    assert non_touch["positions"] == [] and "entry_confirmation" not in non_touch["orders"][0]
    no_reclaim = apply(bar(o=99.5, h=100, low=99, close=99.75))
    assert no_reclaim["positions"] == [] and "entry_confirmation" not in no_reclaim["orders"][0]
    red = apply(bar(o=101, h=102, low=99.5, close=100.5))
    assert red["positions"] == [] and "entry_confirmation" not in red["orders"][0]


def test_retest_that_breaks_structural_stop_is_rejected():
    result = apply(bar(o=99.5, h=101, low=96.9, close=100.5))
    assert result["orders"][0]["status"] == "skipped"
    assert result["orders"][0]["reason"] == "retest_structure_broken"


def test_earlier_registered_structure_break_cannot_be_erased_by_a_later_reclaim():
    result = apply(bar(minute="14:35", o=99.5, h=101, low=96.9, close=100),
                   retest(minute="14:40"), now="2026-11-17T14:45:10+00:00")
    assert result["orders"][0]["status"] == "skipped"
    assert result["orders"][0]["reason"] == "retest_structure_broken"
    assert "entry_confirmation" not in result["orders"][0]


def test_missing_target_and_ineligible_plan_cannot_open():
    missing = candidate()
    missing["target"] = missing["risk_plan"]["target"] = None
    result = apply(retest(), signals=[missing])
    assert result["orders"][0]["reason"] == "missing_target"
    blocked = candidate()
    blocked["risk_plan"].update(eligible=False, reason="missing_nearby_anchor")
    result = apply(retest(), signals=[blocked])
    assert result["orders"][0]["reason"] == "missing_nearby_anchor"


@pytest.mark.parametrize("change", ["missing", "version", "stop", "target"])
def test_missing_or_mismatched_frozen_plan_is_skipped(change):
    value = candidate()
    if change == "missing":
        value["risk_plan"] = None
    else:
        value["risk_plan"][{"version": "strategy_version"}.get(change, change)] = "other" if change == "version" else 90
    result = apply(retest(), signals=[value])
    assert result["positions"] == [] and result["orders"][0]["status"] == "skipped"
    assert result["orders"][0]["reason"] == "entry_plan_mismatch"


def test_actual_gap_price_is_rechecked_against_allowed_zone():
    first = confirmed()
    result = apply(bar(minute="14:40", o=101, h=102, low=100, close=101),
                   bar(minute="14:45", o=103, h=104, low=102, close=103.5),
                   signals=[], state=first, now="2026-11-17T14:50:10+00:00")
    assert result["positions"] == []
    assert result["orders"][0]["reason"] == "entry_above_limit"
    assert result["orders"][0]["entry_checks"]["entry_price"] == 103


def test_fees_and_slippage_can_reject_a_gross_three_r_fill():
    value = candidate()
    value["target"] = value["risk_plan"]["target"] = 113
    first = apply(retest(), signals=[value], config={**CONFIG, "fee_bps": 50, "slippage_bps": 5})
    result = apply(bar(minute="14:40", o=100.5, h=102, low=100, close=101),
                   bar(minute="14:45", o=101, h=102, low=100, close=101.5), signals=[],
                   state=first, config={**CONFIG, "fee_bps": 50, "slippage_bps": 5},
                   now="2026-11-17T14:50:10+00:00")
    assert (113 - 101) / (101 - 97) == 3
    assert result["orders"][0]["reason"] == "entry_rr_below_minimum"
    assert result["orders"][0]["entry_checks"]["net_rr"] < 3
    assert result["positions"] == []


def test_missing_intermediate_bar_invalidates_confirmation_instead_of_bridging():
    result = apply(bar(minute="14:45", o=101, h=102, low=100, close=101.5),
                   signals=[], state=confirmed(), now="2026-11-17T14:50:10+00:00")
    assert result["positions"] == []
    assert result["orders"][0]["reason"] == "retest_coverage_gap"


def test_missing_first_registered_bars_cannot_confirm_a_later_retest():
    result = apply(retest(minute="14:45"), now="2026-11-17T14:50:10+00:00")
    assert result["positions"] == []
    assert result["orders"][0]["status"] == "skipped"
    assert result["orders"][0]["reason"] == "retest_coverage_gap"
    assert "entry_confirmation" not in result["orders"][0]


def test_preconfirmation_watermark_requires_continuity_across_monitor_calls():
    first = apply(bar(o=101, h=103, low=100.5, close=102))
    assert first["orders"][0]["status"] == "pending"
    assert "entry_confirmation" not in first["orders"][0]
    result = apply(retest(minute="14:45"), signals=[], state=first,
                   now="2026-11-17T14:50:10+00:00")
    assert result["orders"][0]["reason"] == "retest_coverage_gap"
    assert "entry_confirmation" not in result["orders"][0]


def test_registration_bound_is_rounded_up_without_requiring_preregistration_quotes():
    value = candidate(observed_at="2026-11-17T14:37:01+00:00")
    result = apply(retest(minute="14:40"), signals=[value], now="2026-11-17T14:45:10+00:00")
    assert result["orders"][0]["reason"] == "retest_confirmed"
    assert result["orders"][0]["entry_confirmation"]["price_bar"]["timestamp"] == "2026-11-17T14:40:00+00:00"


def test_a_missing_stock_interval_cannot_be_hidden_by_a_later_account_watermark():
    first = apply_intraday([candidate()],
                          {"SPX": quotes(bar(), bar(minute="14:40"))["SPX"], "ABC": []},
                          now="2026-11-17T14:45:10+00:00", config=CONFIG,
                          started_at=START, trailing_policy=POLICY)
    result = apply(retest(minute="14:45"), signals=[], state=first,
                   now="2026-11-17T14:50:10+00:00")
    assert result["orders"][0]["reason"] == "retest_coverage_gap"
    assert "entry_confirmation" not in result["orders"][0]


def test_intervening_stop_break_and_cross_session_confirmation_do_not_fill():
    broken = apply(bar(minute="14:40", o=99, h=100, low=96, close=98),
                   signals=[], state=confirmed(), now="2026-11-17T14:45:10+00:00")
    assert broken["orders"][0]["reason"] == "retest_structure_broken"
    next_day = apply(bar(day="2026-11-18", minute="14:30", o=101, h=102, low=100, close=101.5),
                     signals=[], state=confirmed(), now="2026-11-18T14:35:10+00:00")
    assert next_day["positions"] == [] and next_day["orders"][0]["reason"] == "signal_expired"


def test_repeated_quotes_preserve_confirmation_and_fills_and_inputs():
    first = confirmed()
    saved = copy.deepcopy(first)
    again = apply(retest(), signals=[], state=first)
    assert first == saved and again["state"] == first["state"]
    result = filled()
    repeated = apply(bar(minute="14:45", o=101, h=102, low=100, close=101.5),
                     signals=[], state=result, now="2026-11-17T14:50:10+00:00")
    assert repeated["state"] == result["state"]


def test_entry_audit_and_initial_risk_survive_exit():
    first = filled(config={**CONFIG, "fee_bps": 5, "slippage_bps": 5})
    position = first["positions"][0]
    result = apply(bar(minute="14:50", o=102, h=121, low=101, close=120),
                   signals=[], state=first, now="2026-11-17T14:55:10+00:00",
                   config={**CONFIG, "fee_bps": 5, "slippage_bps": 5})
    trade = result["trades"][0]
    assert trade["entry_checks"] == position["entry_checks"]
    assert trade["initial_stop"] == 97
    assert trade["realized_r"] == pytest.approx(trade["net_pnl"] / (trade["quantity"] * trade["initial_risk_per_share"]))
    assert result["cash"] == pytest.approx(100000 + trade["net_pnl"])


def test_cost_aware_position_size_respects_the_frozen_risk_budget():
    position = filled(config={**CONFIG, "fee_bps": 5, "slippage_bps": 5})["positions"][0]
    quantity, risk = position["quantity"], position["initial_net_risk_per_share"]
    assert quantity * risk <= 100000 * .0035
    assert (quantity + 1) * risk > 100000 * .0035
    assert risk > position["initial_risk_per_share"]


def test_v2_policy_must_be_frozen_from_creation_and_config_cannot_migrate():
    with pytest.raises(ValueError, match="creation"):
        apply(policy=None)
    first = confirmed()
    state = copy.deepcopy(first["state"])
    state.pop("trailing_policy")
    with pytest.raises(ValueError, match="creation"):
        apply(state=state)
    with pytest.raises(ValueError, match="configuration"):
        apply(state=first, config=STRUCTURAL)
    with pytest.raises(ValueError, match="policy"):
        apply(state=first, policy={**POLICY, "version": "changed"})


def test_legacy_account_cannot_be_resumed_as_v2_and_fixed_percent_cannot_claim_v2():
    from tests.test_intraday import CONFIG as FIXED
    legacy = apply_intraday([], {}, now=START, config=STRUCTURAL)
    with pytest.raises(ValueError, match="configuration"):
        apply(state=legacy)
    with pytest.raises(ValueError):
        apply(config={**FIXED, "strategy_version": VERSION})
