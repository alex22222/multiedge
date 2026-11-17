"""Independent regressions for portfolio chronology across delayed quote batches."""

from multiedge.intraday import apply_intraday
from tests.test_intraday import bar, signal


CONFIG = {"initial_cash": 100, "risk_fraction": 1, "fee_bps": 0,
          "slippage_bps": 0, "stop_loss_pct": .03, "take_profit_pct": .25}
START = "2026-11-17T14:32:00+00:00"


def reference(*values):
    return [bar(day="2026-11-16", minute="20:55"), *values]


def test_late_symbol_cannot_spend_later_exit_proceeds_at_an_earlier_open():
    first = apply_intraday(
        [signal(ticker="ABC", signal_id="abc"), signal(ticker="XYZ", signal_id="xyz")],
        {"SPX": reference(bar()), "XYZ": [bar(h=126, low=99, close=124)]},
        now="2026-11-17T14:40:00+00:00", config=CONFIG, started_at=START)
    assert first["cash"] == 125
    second = apply_intraday(
        [], {"SPX": reference(bar(), bar(minute="14:40")),
             "ABC": [bar(), bar(minute="14:40")]},
        now="2026-11-17T14:45:00+00:00", config=CONFIG, state=first)
    abc = next(order for order in second["orders"] if order["ticker"] == "ABC")
    assert abc.get("entry_timestamp", "2026-11-17T14:40:00+00:00") >= "2026-11-17T14:40:00+00:00"
    dates = [point["date"] for point in second["equity_curve"]]
    assert dates == sorted(set(dates))
    repeated = apply_intraday(
        [], {"SPX": reference(bar(), bar(minute="14:40")),
             "ABC": [bar(), bar(minute="14:40")]},
        now="2026-11-17T14:45:00+00:00", config=CONFIG, state=second)
    assert repeated["state"] == second["state"]


def test_delayed_protective_stop_reconciles_without_rewinding_equity_curve():
    config = {**CONFIG, "initial_cash": 200, "risk_fraction": .015}
    first = apply_intraday(
        [signal(ticker="ABC", signal_id="abc"), signal(ticker="XYZ", signal_id="xyz")],
        {"SPX": reference(bar()), "ABC": [bar()]},
        now="2026-11-17T14:40:00+00:00", config=config, started_at=START)
    second = apply_intraday(
        [], {"SPX": reference(bar(), bar(minute="14:40")),
             "XYZ": [bar(minute="14:40", h=126, low=99, close=124)]},
        now="2026-11-17T14:45:00+00:00", config=config, state=first)
    recovered = apply_intraday(
        [], {"SPX": reference(bar(), bar(minute="14:40"), bar(minute="14:45")),
             "ABC": [bar(minute="14:40", h=102, low=96, close=98), bar(minute="14:45")]},
        now="2026-11-17T14:50:00+00:00", config=config, state=second)
    trade = next(trade for trade in recovered["trades"] if trade["ticker"] == "ABC")
    assert trade["exit_timestamp"] == "2026-11-17T14:45:00+00:00"
    assert trade["exit_reason"] == "stop"
    assert recovered["cash"] == 222
    dates = [point["date"] for point in recovered["equity_curve"]]
    assert dates == sorted(set(dates))


def test_rolling_calendar_cannot_bridge_unknown_intermediate_sessions():
    first = apply_intraday(
        [signal(signal_date="2026-11-05", observed_at="2026-11-06T20:52:00+00:00")],
        {"SPX": [bar(day="2026-11-05", minute="20:55"), bar(day="2026-11-06", minute="20:55")],
         "ABC": [bar(day="2026-11-06", minute="20:55")]},
        now="2026-11-06T21:05:00+00:00", config=CONFIG,
        started_at="2026-11-06T20:52:00+00:00")
    rolling = [bar(day=day, minute="14:30") for day in
               ("2026-11-11", "2026-11-12", "2026-11-13", "2026-11-16")]
    resumed = apply_intraday(
        [], {"SPX": [*rolling, bar(minute="14:35")],
             "ABC": [bar(day="2026-11-11", minute="14:30", o=110, h=126, low=109, close=124)]},
        now="2026-11-17T14:45:00+00:00", config=CONFIG, state=first)
    assert resumed["trades"] == []
    assert resumed["positions"][0]["mark_timestamp"] == "2026-11-06T21:00:00+00:00"
    assert "ABC" in resumed["coverage_gaps"]
