"""Structural exits use the completed signal's chart levels, never fixed percentages."""
import copy

import pytest

from multiedge.intraday import apply_intraday
from tests.test_intraday import CONFIG, bar, frames, signal


STRUCTURAL = {k: CONFIG[k] for k in ("initial_cash", "risk_fraction", "fee_bps", "slippage_bps")}
STRUCTURAL["exit_mode"] = "structural"
PLAN = {"basis": "frozen_engine_structure", "source_run_id": "run-1", "signal_date": "2026-11-16",
        "stop_anchor": 98, "anchor_date": "2026-11-12", "stop": 97, "target": 115}


def run(signals=None, data=None, **kwargs):
    return apply_intraday(signals if signals is not None else [signal(stop=97, target=115, risk_plan=PLAN)],
                          frames(bar()) if data is None else data,
                          now=kwargs.pop("now", "2026-11-17T14:40:00+00:00"),
                          config=kwargs.pop("config", STRUCTURAL),
                          started_at="2026-11-17T14:32:00+00:00", **kwargs)


def test_structural_queue_keeps_absolute_levels_and_source_without_fake_entry_plan():
    result = run(data={})
    order = result["orders"][0]
    assert order["stop"] == 97 and order["target"] == 115
    assert order["risk_plan"] == PLAN and order["exit_mode"] == "structural"
    assert order["status"] == "pending" and order["planned_rr"] is None
    assert result["positions"] == []


def test_structural_actual_entry_determines_risk_distance_position_size_and_rr():
    result = run(config={**STRUCTURAL, "fee_bps": 5, "slippage_bps": 5})
    p = result["positions"][0]
    assert p["entry_price"] == pytest.approx(100.05)
    assert p["stop"] == 97 and p["target"] == 115
    assert p["quantity"] == 114
    assert p["planned_stop_pct"] == pytest.approx(3.05 / 100.05)
    assert p["planned_target_pct"] == pytest.approx(14.95 / 100.05)
    assert p["planned_rr"] == pytest.approx(14.95 / 3.05)
    assert p["risk_plan"] == PLAN
    assert p["stop"] != pytest.approx(p["entry_price"] * .97)


def test_two_stocks_use_different_structure_distances_not_one_percentage():
    result = run([signal(ticker="ABC", signal_id="a", stop=97, target=115),
                  signal(ticker="XYZ", signal_id="b", stop=94, target=120)],
                 {**frames(bar()), "XYZ": [bar()]})
    positions = {p["ticker"]: p for p in result["positions"]}
    assert positions["ABC"]["planned_stop_pct"] == pytest.approx(.03)
    assert positions["XYZ"]["planned_stop_pct"] == pytest.approx(.06)
    assert positions["ABC"]["quantity"] > positions["XYZ"]["quantity"]
    assert positions["ABC"]["target"] == 115 and positions["XYZ"]["target"] == 120


@pytest.mark.parametrize("stop", [None, 0, -1, 100, 101])
def test_missing_or_broken_structural_stop_remains_recorded_as_skipped(stop):
    result = run([signal(stop=stop, target=115)])
    assert result["orders"][0]["status"] == "skipped"
    assert result["orders"][0]["reason"] == "invalid_stop"
    assert result["positions"] == result["trades"] == []


def test_gap_above_target_is_skipped_instead_of_creating_replacement_target():
    result = run([signal(stop=97, target=100.02)], config={**STRUCTURAL, "slippage_bps": 5})
    assert result["orders"][0]["reason"] == "target_at_or_below_entry"
    assert result["positions"] == []


def test_missing_target_tracks_only_structural_stop_and_does_not_invent_25_percent():
    initial = run([signal(stop=97, target=None)])
    position = initial["positions"][0]
    assert position["target"] is None and position["planned_rr"] is None
    assert position["planned_target_pct"] is None
    next_result = run([], frames(bar(minute="14:40", o=130, h=140, low=125, close=135)),
                      state=initial, now="2026-11-17T14:45:00+00:00")
    assert len(next_result["positions"]) == 1 and next_result["trades"] == []
    stopped = run([], frames(bar(minute="14:45", o=96, h=99, low=95, close=98)),
                  state=next_result, now="2026-11-17T14:50:00+00:00")
    assert stopped["trades"][0]["exit_price"] == 96
    assert stopped["trades"][0]["exit_reason"] == "stop_gap"


def test_structural_exit_keeps_frozen_anchor_provenance_and_fees():
    initial = run(config={**STRUCTURAL, "fee_bps": 5, "slippage_bps": 5})
    saved = copy.deepcopy(initial)
    exited = run([], frames(bar(minute="14:40", o=110, h=116, low=109, close=114)),
                 config={**STRUCTURAL, "fee_bps": 5, "slippage_bps": 5},
                 state=initial, now="2026-11-17T14:45:00+00:00")
    assert initial == saved
    trade = exited["trades"][0]
    assert trade["stop"] == 97 and trade["target"] == 115
    assert trade["exit_price"] == pytest.approx(115 * .9995)
    assert trade["risk_plan"] == PLAN
    assert all(event["risk_plan"] == PLAN for event in exited["events"] if event["kind"] in ("entry", "exit"))
    assert exited["cash"] == pytest.approx(100000 + trade["net_pnl"])
    repeated = run([], frames(bar(minute="14:40", o=110, h=116, low=109, close=114)),
                   config={**STRUCTURAL, "fee_bps": 5, "slippage_bps": 5},
                   state=exited, now="2026-11-17T14:45:00+00:00")
    assert repeated["state"] == exited["state"]


def test_changed_structural_anchor_cannot_rewrite_existing_signal():
    queued = run(data={})
    with pytest.raises(ValueError, match="signal_id"):
        run([signal(stop=96, target=115, risk_plan=PLAN)], state=queued)


def test_existing_fixed_percent_state_and_fingerprints_resume_without_migration():
    initial = apply_intraday([signal(stop=97, target=115, risk_plan=PLAN)], frames(bar()),
                             now="2026-11-17T14:40:00+00:00", config=CONFIG,
                             started_at="2026-11-17T14:32:00+00:00")
    assert initial["config"] == CONFIG and "exit_mode" not in initial["state"]["config"]
    resumed = apply_intraday([signal(stop=95, target=120, risk_plan={"different": "ignored"})], frames(bar()),
                             now="2026-11-17T14:40:00+00:00", config=CONFIG, state=initial)
    assert resumed["state"] == initial["state"]


def test_structural_parameters_cannot_be_replaced_on_resume():
    initial = run()
    with pytest.raises(ValueError, match="configuration"):
        apply_intraday([], {}, now="2026-11-17T14:45:00+00:00", config=CONFIG, state=initial)


def test_structural_config_rejects_fixed_percent_mix():
    with pytest.raises(ValueError):
        run(config={**STRUCTURAL, "stop_loss_pct": .03})
