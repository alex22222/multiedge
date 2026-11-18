"""Registered trailing decisions affect only later bars and preserve initial risk."""
import copy
from datetime import datetime, timedelta

import pytest

from multiedge.intraday import apply_intraday
from tests.test_intraday import bar, signal
from tests.test_intraday_structural import STRUCTURAL, PLAN


POLICY = {"version": "structure_ma20_hl_v1", "enabled_at": "2026-11-17T22:00:00+00:00"}


def held():
    start = datetime.fromisoformat("2026-11-17T14:35:00+00:00")
    values = [bar(minute=(start + timedelta(minutes=5 * i)).strftime("%H:%M")) for i in range(77)]
    return apply_intraday([signal(stop=97, target=115, risk_plan=PLAN)],
                          {"SPX": [bar(day="2026-11-16", minute="20:55"), *values], "ABC": values},
                          now="2026-11-17T21:00:00+00:00", config=STRUCTURAL,
                          started_at="2026-11-17T14:32:00+00:00")


def proposal(**values):
    return {"update_id": "update-1", "ticker": "ABC", "source_signal_id": "s1", "new_stop": 103,
            "source_as_of": "2026-11-17", "source_completed_at": "2026-11-17T21:30:00+00:00",
            "observed_at": "2026-11-17T22:00:00+00:00", "effective_at": "2026-11-18T14:30:00+00:00",
            "policy_version": POLICY["version"], "reason": "20MA带和已确认更高低点上移",
            "evidence": {"ma20": 104, "confirmed_pivot_low": 104.5, "buffer": 1}, **values}


def data(*values):
    return {"SPX": [bar(minute="20:55"), *values], "ABC": list(values)}


def apply(state, *, now="2026-11-17T22:00:00+00:00", bars=None, signals=None, **values):
    return apply_intraday(signals or [], bars or {}, now=now, config=STRUCTURAL, state=state, **values)


def enabled(update=None):
    return apply(held(), trailing_policy=POLICY, stop_updates=[update or proposal()])


def test_enable_preserves_economics_and_freezes_original_risk_without_rewriting_history():
    original = held()
    saved = copy.deepcopy(original)
    result = apply(original, trailing_policy=POLICY)
    assert original == saved
    for key in ("cash", "equity", "trades", "equity_curve"):
        assert result[key] == original[key]
    p = result["positions"][0]
    assert p["stop"] == p["initial_stop"] == 97
    assert p["initial_risk_per_share"] == 3
    assert p["planned_rr"] == original["positions"][0]["planned_rr"]
    assert p["risk_plan"] == PLAN
    assert result["events"][-1]["kind"] == "trailing_enabled"
    assert result["events"][-1]["timestamp"] == POLICY["enabled_at"]


def test_new_entry_under_enabled_policy_stores_initial_risk_without_changing_entry_rules():
    policy = {**POLICY, "enabled_at": "2026-11-17T14:32:00+00:00"}
    result = apply_intraday([signal(stop=97, target=115, risk_plan=PLAN)],
                            {"SPX": [bar(day="2026-11-16"), bar()], "ABC": [bar()]},
                            now="2026-11-17T14:40:00+00:00", config=STRUCTURAL,
                            started_at=policy["enabled_at"], trailing_policy=policy)
    p = result["positions"][0]
    assert p["entry_timestamp"] == "2026-11-17T14:35:00+00:00"
    assert p["initial_stop"] == 97 and p["initial_risk_per_share"] == 3
    assert p["stop_adjustments"] == [] and result["stop_adjustments"] == []


def test_update_waits_for_next_session_and_logs_every_applied_basis():
    first = enabled()
    assert first["stop_updates"][0]["status"] == "pending"
    assert first["positions"][0]["stop"] == 97 and first["stop_adjustments"] == []
    candle = bar(day="2026-11-18", minute="14:30", o=110, h=112, low=109, close=111)
    result = apply(first, now="2026-11-18T14:35:00+00:00", bars=data(candle))
    p = result["positions"][0]
    assert p["stop"] == 103 and p["initial_stop"] == 97
    adjustment = result["stop_adjustments"][0]
    assert adjustment["old_stop"] == 97 and adjustment["new_stop"] == 103
    assert adjustment["reason"] == proposal()["reason"]
    assert adjustment["evidence"] == proposal()["evidence"]
    assert adjustment["effective_at"] == candle["timestamp"]
    assert adjustment["requested_effective_at"] == proposal()["effective_at"]
    assert adjustment["recorded_at"] == "2026-11-18T14:35:00+00:00"
    assert p["stop_adjustments"] == [adjustment]
    assert result["stop_updates"][0]["status"] == "applied"


def test_duplicate_update_and_duplicate_quote_do_not_duplicate_adjustment():
    first = enabled()
    repeated = apply(first, trailing_policy=POLICY, stop_updates=[proposal()])
    assert repeated["state"] == first["state"]
    candle = bar(day="2026-11-18", minute="14:30", o=110, h=112, low=109, close=111)
    applied = apply(first, now="2026-11-18T14:35:00+00:00", bars=data(candle))
    again = apply(applied, now="2026-11-18T14:35:00+00:00", bars=data(candle), stop_updates=[proposal()])
    assert again["state"] == applied["state"]
    with pytest.raises(ValueError, match="update_id"):
        apply(first, stop_updates=[proposal(new_stop=104)])


def test_lower_stop_is_not_applied_but_decision_remains_auditable():
    first = enabled(proposal(new_stop=96))
    candle = bar(day="2026-11-18", minute="14:30", o=110, h=112, low=109, close=111)
    result = apply(first, now="2026-11-18T14:35:00+00:00", bars=data(candle))
    assert result["positions"][0]["stop"] == 97
    assert result["stop_updates"][0]["status"] == "no_change"
    assert result["stop_adjustments"] == []


@pytest.mark.parametrize("stop,exit_price", [(100, 100), (105, 105)])
def test_breakeven_or_profit_stop_keeps_realized_r_based_on_initial_risk(stop, exit_price):
    first = enabled(proposal(new_stop=stop))
    candle = bar(day="2026-11-18", minute="14:30", o=110, h=112, low=99, close=111)
    result = apply(first, now="2026-11-18T14:35:00+00:00", bars=data(candle))
    trade = result["trades"][0]
    assert trade["exit_price"] == exit_price
    assert trade["initial_stop"] == 97 and trade["initial_risk_per_share"] == 3
    assert trade["realized_r"] == pytest.approx((exit_price - 100) / 3)
    assert trade["exit_reason"] == "trailing_stop"
    assert len(trade["stop_adjustments"]) == 1


def test_open_below_new_stop_exits_at_actual_open_not_virtual_stop():
    first = enabled(proposal(new_stop=105))
    candle = bar(day="2026-11-18", minute="14:30", o=102, h=104, low=101, close=103)
    result = apply(first, now="2026-11-18T14:35:00+00:00", bars=data(candle))
    assert result["trades"][0]["exit_price"] == 102
    assert result["trades"][0]["exit_reason"] == "trailing_stop_gap"
    assert result["stop_adjustments"][0]["new_stop"] == 105


def test_old_stop_gap_exits_before_an_unnecessary_new_adjustment():
    first = enabled(proposal(new_stop=105))
    candle = bar(day="2026-11-18", minute="14:30", o=96, h=98, low=95, close=97)
    result = apply(first, now="2026-11-18T14:35:00+00:00", bars=data(candle))
    assert result["trades"][0]["exit_reason"] == "stop_gap"
    assert result["stop_adjustments"] == []
    assert result["stop_updates"][0]["status"] == "position_closed"


def test_enable_does_not_apply_new_stop_to_earlier_unseen_bars():
    first = held()
    policy = {**POLICY, "enabled_at": "2026-11-18T14:37:00+00:00"}
    update = proposal(observed_at=policy["enabled_at"], effective_at="2026-11-18T14:40:00+00:00")
    candle = bar(day="2026-11-18", minute="14:30", o=110, h=112, low=96, close=111)
    result = apply(first, now=policy["enabled_at"], bars=data(candle), trailing_policy=policy, stop_updates=[update])
    assert result["trades"][0]["exit_price"] == 97
    assert result["trades"][0]["exit_reason"] == "stop"
    assert result["stop_adjustments"] == []


def test_missing_opening_bar_pauses_adjustment_until_missing_coverage_recovers():
    first = enabled()
    opening = bar(day="2026-11-18", minute="14:30", o=110, h=112, low=109, close=111)
    later = bar(day="2026-11-18", minute="14:35", o=111, h=113, low=110, close=112)
    missing = apply(first, now="2026-11-18T14:40:00+00:00", bars=data(later))
    assert missing["positions"][0]["stop"] == 97 and missing["stop_adjustments"] == []
    recovered = apply(missing, now="2026-11-18T14:40:00+00:00", bars=data(opening, later))
    assert recovered["positions"][0]["stop"] == 103
    assert recovered["stop_adjustments"][0]["effective_at"] == opening["timestamp"]


def test_closed_position_update_cannot_affect_same_ticker_reentry():
    first = enabled(proposal(new_stop=105))
    candle = bar(day="2026-11-18", minute="14:30", o=96, h=98, low=95, close=97)
    new_signal = signal(signal_id="s2", signal_date="2026-11-17", observed_at="2026-11-17T22:00:00+00:00", stop=90, target=None)
    result = apply(first, now="2026-11-18T14:35:00+00:00", bars=data(candle), signals=[new_signal])
    assert result["positions"][0]["source_signal_id"] == "s2"
    assert result["positions"][0]["stop"] == 90
    assert result["stop_updates"][0]["status"] == "position_closed"


@pytest.mark.parametrize("changes", [
    {"observed_at": "2026-11-18T22:00:00+00:00"},
    {"source_completed_at": "2026-11-18T21:30:00+00:00"},
    {"effective_at": "2026-11-17T21:00:00+00:00"},
    {"observed_at": "2026-11-17T22:00:00"},
    {"source_as_of": "2026-11-16", "source_completed_at": "2026-11-16T21:30:00+00:00"},
])
def test_future_or_inconsistent_decision_evidence_is_rejected(changes):
    with pytest.raises(ValueError):
        enabled(proposal(**changes))


def test_policy_is_immutable_and_only_supported_for_structural_accounts():
    first = enabled()
    with pytest.raises(ValueError, match="policy"):
        apply(first, trailing_policy={**POLICY, "version": "changed"})
    from tests.test_intraday import CONFIG
    with pytest.raises(ValueError):
        apply_intraday([], {}, now=POLICY["enabled_at"], config=CONFIG, trailing_policy=POLICY)
