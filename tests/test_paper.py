"""Paper fills are causal, gap-aware and resumable."""
import copy
import json

import pandas as pd
import pytest

from multiedge.paper import run_paper


def bars(rows):
    return pd.DataFrame(rows, columns=["Date", "Open", "High", "Low", "Close"]).set_index("Date").rename_axis("Date").pipe(
        lambda df: df.set_axis(pd.to_datetime(df.index)))


def signal(**changes):
    result = {"ticker": "ABC", "signal_date": "2025-01-06", "stop": 95.0,
              "target": 110.0, "signal_id": "signal-1", "observed_at": "2025-01-06T22:00:00Z"}
    result.update(changes)
    return result


def run(signals, frames, as_of="2025-01-08", **kwargs):
    return run_paper(signals, frames, as_of=as_of, initial_cash=10000,
                     risk_fraction=.01, fee_bps=0, slippage_bps=0, **kwargs)


def normal_frames():
    return {"ABC": bars([
        ("2025-01-06", 100, 102, 98, 100),
        ("2025-01-07", 100, 106, 96, 105),
        ("2025-01-08", 105, 109, 100, 108),
    ])}


def test_signal_close_cannot_fill_on_same_day_and_equity_reconciles():
    frames = normal_frames()
    before = run([signal()], frames, "2025-01-06")
    assert before["positions"] == [] and before["cash"] == 10000
    result = run([signal()], frames)
    position = result["positions"][0]
    assert position["entry_date"] == "2025-01-07"
    assert position["quantity"] == 20
    assert result["cash"] == 8000
    assert result["equity"] == 10160
    assert result["metrics"]["unrealized_pnl"] == 160
    assert result["trades"] == []


def test_gap_through_stop_fills_at_open_not_stale_stop():
    frames = normal_frames()
    frames["ABC"].loc[pd.Timestamp("2025-01-08")] = [90, 96, 88, 92]
    result = run([signal()], frames)
    trade = result["trades"][0]
    assert trade["exit_price"] == 90 and trade["exit_reason"] == "stop_gap"
    assert trade["net_pnl"] == -200 and result["equity"] == 9800


def test_both_barriers_touched_is_conservative_stop_first_on_entry_day():
    frames = normal_frames()
    frames["ABC"].loc[pd.Timestamp("2025-01-07")] = [100, 115, 94, 108]
    result = run([signal()], frames)
    assert result["trades"][0]["exit_reason"] == "stop_and_target"
    assert result["trades"][0]["exit_price"] == 95
    assert result["equity"] == 9900


def test_missing_target_allows_stop_only_position_and_does_not_force_close():
    frames = normal_frames()
    frames["ABC"].loc[pd.Timestamp("2025-01-08")] = [105, 200, 100, 120]
    result = run([signal(target=None)], frames)
    assert result["positions"][0]["target"] is None
    assert result["trades"] == [] and result["equity"] == 10400


@pytest.mark.parametrize("stop,target,reason", [
    (None, 110, "invalid_stop"), (105, 110, "stop_at_or_above_entry"),
    (95, 99, "target_at_or_below_entry"),
])
def test_unexecutable_signals_remain_auditable(stop, target, reason):
    result = run([signal(stop=stop, target=target)], normal_frames())
    assert result["positions"] == [] and result["trades"] == []
    assert result["orders"][0]["status"] == "skipped"
    assert result["orders"][0]["reason"] == reason


def test_fees_slippage_and_closed_pnl_reconcile_with_cash():
    frames = normal_frames()
    frames["ABC"].loc[pd.Timestamp("2025-01-07")] = [100, 115, 96, 108]
    result = run_paper([signal()], frames, as_of="2025-01-08", initial_cash=10000,
                       risk_fraction=.01, fee_bps=100, slippage_bps=100)
    trade = result["trades"][0]
    assert trade["quantity"] == 16 and trade["entry_price"] == 101
    assert trade["exit_price"] == pytest.approx(108.9)
    assert trade["net_pnl"] == pytest.approx(92.816)
    assert result["cash"] == pytest.approx(10092.816)
    assert result["metrics"]["fees_paid"] == pytest.approx(33.584)


def test_missing_ticker_entry_session_does_not_fill_a_later_bar():
    frames = normal_frames()
    frames["SPX"] = frames["ABC"].copy()
    frames["ABC"] = frames["ABC"].drop(pd.Timestamp("2025-01-07"))
    result = run([signal()], frames)
    assert result["positions"] == []
    assert result["orders"][0]["reason"] == "missing_entry_bar"


def test_stale_marks_keep_actual_mark_date_and_are_flagged():
    frames = normal_frames()
    frames["SPX"] = frames["ABC"].copy()
    frames["ABC"] = frames["ABC"].iloc[:2]
    result = run([signal()], frames)
    position = result["positions"][0]
    assert position["mark_date"] == "2025-01-07" and position["stale"]
    assert result["equity"] == 10100
    assert result["metrics"]["stale_positions"] == 1


def test_resume_is_idempotent_json_serializable_and_equivalent_to_one_run():
    frames = normal_frames()
    frames["ABC"].loc[pd.Timestamp("2025-01-08")] = [90, 96, 88, 92]
    first = run([signal()], frames, "2025-01-07")
    state = json.loads(json.dumps(first["state"], allow_nan=False))
    original = copy.deepcopy(state)
    resumed = run([signal(), signal()], frames, state=state)
    full = run([signal()], frames)
    assert state == original
    for key in ("trades", "cash", "equity", "events", "equity_curve"):
        assert resumed[key] == full[key]
    again = run([signal()], frames, state=resumed)
    assert again == resumed


def test_forward_rejects_late_or_undated_signal_without_retroactive_fill():
    frames = normal_frames()
    late = run([signal(observed_at="2025-01-07T14:31:00Z")], frames, mode="forward")
    missing = run([signal(observed_at=None)], frames, mode="forward")
    assert late["orders"][0]["reason"] == "signal_observed_after_entry"
    assert missing["orders"][0]["reason"] == "missing_observed_at"
    timely = run([signal(observed_at="2025-01-07T14:29:00Z")], frames, mode="forward")
    assert timely["positions"][0]["entry_date"] == "2025-01-07"


def test_cash_cap_prevents_leverage():
    result = run_paper([signal()], normal_frames(), as_of="2025-01-08", initial_cash=100,
                       risk_fraction=1, fee_bps=5, slippage_bps=5)
    assert result["cash"] >= 0 and not result["positions"]
    assert result["orders"][0]["reason"] == "insufficient_budget"


def test_resume_rejects_accounting_changes_and_modified_signal_identity():
    first = run([signal()], normal_frames(), "2025-01-07")
    with pytest.raises(ValueError, match="configuration"):
        run_paper([signal()], normal_frames(), as_of="2025-01-08", state=first["state"])
    with pytest.raises(ValueError, match="signal_id"):
        run([signal(stop=96)], normal_frames(), state=first["state"])


def test_new_signal_import_cannot_rewrite_processed_history():
    first = run([], normal_frames(), "2025-01-08")
    second = run([signal()], normal_frames(), state=first["state"])
    assert second["positions"] == [] and second["cash"] == 10000
    assert second["orders"][0]["reason"] == "late_import"


def test_slippage_cannot_resurrect_a_setup_already_open_below_its_stop():
    result = run_paper([signal(stop=100.02)], normal_frames(), as_of="2025-01-08",
                       slippage_bps=5)
    assert not result["positions"]
    assert result["orders"][0]["reason"] == "stop_at_or_above_entry"


def test_malformed_target_is_not_silently_converted_to_stop_only():
    with pytest.raises(ValueError, match="target"):
        run([signal(target="not a price")], normal_frames())


def test_forward_accepts_real_availability_after_an_early_close():
    # The upstream source owns completed-session verification; a hardcoded
    # 16:00 close would reject legitimate 13:00 exchange closes.
    frames = normal_frames()
    frames["ABC"].index = pd.to_datetime(["2025-11-28", "2025-12-01", "2025-12-02"])
    result = run([signal(signal_date="2025-11-28", observed_at="2025-11-28T18:15:00Z")],
                 frames, "2025-12-02", mode="forward")
    assert result["positions"][0]["entry_date"] == "2025-12-01"


def test_forward_curve_does_not_backfill_cash_before_registration():
    result = run([signal(observed_at="2025-01-08T15:00:00Z")], normal_frames(), mode="forward")
    assert result["state"]["start_date"] == "2025-01-08"
    assert [point["date"] for point in result["equity_curve"]] == ["2025-01-08"]
    assert result["orders"][0]["reason"] == "signal_observed_after_entry"


def test_empty_forward_account_starts_at_as_of_without_historical_cash_points():
    result = run([], normal_frames(), mode="forward")
    assert result["state"]["start_date"] == "2025-01-08"
    assert [point["date"] for point in result["equity_curve"]] == ["2025-01-08"]


def test_explicit_started_at_defines_start_and_survives_resume():
    first = run([signal()], normal_frames(), "2025-01-07", mode="forward", started_at="2025-01-07T14:29:00Z")
    assert first["state"]["start_date"] == "2025-01-07"
    assert first["positions"][0]["entry_date"] == "2025-01-07"
    resumed = run([signal()], normal_frames(), mode="forward", started_at="2025-01-08T18:00:00Z", state=first)
    assert resumed["state"]["start_date"] == "2025-01-07"
    assert resumed["state"]["started_at"] == first["state"]["started_at"]
    assert [point["date"] for point in resumed["equity_curve"]] == ["2025-01-07", "2025-01-08"]


def test_explicit_registration_after_open_cannot_fill_older_available_signal():
    result = run([signal()], normal_frames(), mode="forward", started_at="2025-01-07T14:31:00Z")
    assert result["positions"] == []
    assert result["orders"][0]["reason"] == "account_started_after_entry"


def test_registration_after_latest_quote_does_not_create_earlier_equity():
    result = run([], normal_frames(), "2025-01-07", mode="forward", started_at="2025-01-08T15:00:00Z")
    assert result["equity_curve"] == []
    assert result["state"]["start_date"] == "2025-01-08"
    assert result["equity"] == 10000


@pytest.mark.parametrize("stamp", ["2025-01-07T14:29:00", "not-a-timestamp"])
def test_forward_registration_requires_a_valid_timezone_timestamp(stamp):
    with pytest.raises(ValueError, match="started_at"):
        run([], normal_frames(), mode="forward", started_at=stamp)


def test_unknown_legacy_history_is_flagged_and_not_silently_rewritten_on_later_resume():
    first = run([], normal_frames(), "2025-01-07", mode="forward")
    legacy = first["state"]
    legacy.pop("start_date")
    legacy.pop("started_at")
    legacy["equity_curve"].insert(0, {"date": "2025-01-06", "cash": 10000,
                                     "equity": 10000, "stale_positions": 0})
    resumed = run([], normal_frames(), mode="forward", state=legacy)
    assert resumed["state"]["history_start_unverified"]
    again = run([], normal_frames(), mode="forward", state=resumed)
    assert again["equity_curve"] == resumed["equity_curve"]
    assert again["equity_curve"][0]["date"] == "2025-01-06"


def test_start_date_without_timestamp_cannot_leave_an_old_entry_pending():
    first = run([], normal_frames(), "2025-01-06", mode="forward")
    first["state"]["start_date"] = "2025-01-08"
    result = run([signal()], normal_frames(), mode="forward", state=first)
    assert result["orders"][0]["status"] == "skipped"
    assert result["orders"][0]["reason"] == "account_started_after_entry"


@pytest.mark.parametrize("stop_pct,rr", [(.03, 25 / 3), (.05, 5)])
def test_fixed_exit_plan_uses_actual_entry_including_slippage(stop_pct, rr):
    frames = {"ABC": bars([
        ("2025-01-06", 100, 102, 98, 100),
        ("2025-01-07", 110, 115, 108, 112),
        ("2025-01-08", 112, 120, 108, 118),
    ])}
    result = run_paper([signal(stop=None, target=None)], frames, as_of="2025-01-08",
                       fee_bps=0, slippage_bps=100, exit_mode="fixed_percent",
                       stop_loss_pct=stop_pct, take_profit_pct=.25)
    position = result["positions"][0]
    assert position["entry_price"] == pytest.approx(111.1)
    assert position["stop"] == pytest.approx(111.1 * (1 - stop_pct))
    assert position["target"] == pytest.approx(111.1 * 1.25)
    assert position["exit_mode"] == "fixed_percent"
    assert position["planned_stop_pct"] == stop_pct
    assert position["planned_target_pct"] == .25
    assert position["planned_rr"] == pytest.approx(rr)
    assert result["state"]["config"]["exit_mode"] == "fixed_percent"


def test_fixed_plan_ignores_unavailable_or_invalid_structural_exits():
    frames = normal_frames()
    frames["ABC"].loc[pd.Timestamp("2025-01-07")] = [100, 106, 98, 105]
    result = run([signal(stop=200, target="not a structural target")], frames,
                 exit_mode="fixed_percent", stop_loss_pct=.03, take_profit_pct=.25)
    assert result["orders"][0]["status"] == "filled"
    assert result["positions"][0]["stop"] == 97
    assert result["positions"][0]["target"] == 125


@pytest.mark.parametrize("stop_pct", [.03, .05])
def test_fixed_percent_target_closes_at_true_bar_touch(stop_pct):
    frames = normal_frames()
    frames["ABC"].loc[pd.Timestamp("2025-01-07")] = [100, 126, 99, 120]
    result = run([signal(stop=None, target=None)], frames, exit_mode="fixed_percent",
                 stop_loss_pct=stop_pct, take_profit_pct=.25)
    trade = result["trades"][0]
    assert trade["exit_reason"] == "target" and trade["exit_price"] == 125
    assert trade["exit_mode"] == "fixed_percent" and trade["planned_target_pct"] == .25
    assert result["positions"] == []


def test_fixed_plan_stop_first_and_gap_exit_keep_real_price_rules():
    frames = normal_frames()
    frames["ABC"].loc[pd.Timestamp("2025-01-07")] = [100, 126, 96, 110]
    both = run([signal()], frames, exit_mode="fixed_percent", stop_loss_pct=.03, take_profit_pct=.25)
    assert both["trades"][0]["exit_reason"] == "stop_and_target"
    assert both["trades"][0]["exit_price"] == 97
    frames["ABC"].loc[pd.Timestamp("2025-01-07")] = [100, 106, 98, 105]
    frames["ABC"].loc[pd.Timestamp("2025-01-08")] = [90, 94, 88, 92]
    gap = run([signal()], frames, exit_mode="fixed_percent", stop_loss_pct=.03, take_profit_pct=.25)
    assert gap["trades"][0]["exit_reason"] == "stop_gap"
    assert gap["trades"][0]["exit_price"] == 90


@pytest.mark.parametrize("params", [
    {"stop_loss_pct": 0, "take_profit_pct": .25},
    {"stop_loss_pct": 1, "take_profit_pct": .25},
    {"stop_loss_pct": True, "take_profit_pct": .25},
    {"stop_loss_pct": .03, "take_profit_pct": None},
    {"stop_loss_pct": .03, "take_profit_pct": float("nan")},
    {"stop_loss_pct": .03, "take_profit_pct": -.25},
])
def test_fixed_percent_plan_rejects_invalid_parameters(params):
    with pytest.raises(ValueError, match="percent|pct"):
        run([], normal_frames(), exit_mode="fixed_percent", **params)


def test_exit_plan_resume_is_immutable_and_structural_config_stays_compatible():
    structural = run([], normal_frames())
    assert set(structural["state"]["config"]) == {"initial_cash", "risk_fraction", "fee_bps", "slippage_bps"}
    first = run([], normal_frames(), "2025-01-07", exit_mode="fixed_percent",
                stop_loss_pct=.03, take_profit_pct=.25)
    again = run([], normal_frames(), exit_mode="fixed_percent", stop_loss_pct=.03,
                take_profit_pct=.25, state=first)
    assert run([], normal_frames(), exit_mode="fixed_percent", stop_loss_pct=.03,
               take_profit_pct=.25, state=again) == again
    with pytest.raises(ValueError, match="configuration"):
        run([], normal_frames(), exit_mode="fixed_percent", stop_loss_pct=.05,
            take_profit_pct=.25, state=first)
