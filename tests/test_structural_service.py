"""Automatic Paper uses each stock's saved structure, not example percentages."""
from datetime import datetime
from pathlib import Path

import pytest

from multiedge.dashboard import ResearchService
from multiedge.research import build_local_run
from tests.test_monitor import bar, quotes


DATA = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture
def source(tmp_path, monkeypatch):
    service = ResearchService(DATA, tmp_path)
    run = build_local_run(["GOOGL"], "2025-08-01", DATA)
    run["imported_at"] = "2025-08-01T22:00:00+00:00"
    service.store.save_run(run)
    monkeypatch.setattr("multiedge.dashboard.utc_now", lambda: "2025-08-01T22:00:00+00:00")
    monkeypatch.setattr("multiedge.dashboard.latest_complete_date", lambda: "2025-08-01")
    return service, run


def test_default_forward_plan_uses_saved_levels_and_key_candle_provenance(source):
    service, run = source
    account = service.paper(run["run_id"], mode="forward")
    assert account["execution_model"] == "intraday_5m"
    assert account["exit_mode"] == "structural"
    order = account["orders"][0]
    assert order["stop"] == 184.91 and order["target"] == 207.05
    assert order["risk_plan"]["anchor_date"] == "2025-07-21"
    assert order["risk_plan"]["stop_anchor"] == 186.15
    assert order["risk_plan"]["source_run_id"] == run["run_id"]
    assert order["risk_plan"]["buffer_atr"] == .1
    assert "stop_loss_pct" not in account["config"]
    legacy = service.paper(run["run_id"], mode="forward", execution_model="daily_ohlc")
    assert legacy["account_id"] != account["account_id"]


def test_structural_monitor_freezes_report_levels_and_computes_actual_entry_risk(source, monkeypatch):
    service, run = source
    account = service.paper(run["run_id"], mode="forward")
    candle = {**bar(low=189, high=192, close=191), "open": 190}
    monkeypatch.setattr("multiedge.intraday.fetch_yahoo_intraday", lambda *a, **k: quotes(candle))
    receipt = service.monitor_paper(now=datetime.fromisoformat("2025-08-04T13:35:00+00:00"))
    assert receipt["status"] == "completed"
    updated = service.store.get_paper(account["account_id"])
    position = updated["positions"][0]
    assert position["entry_price"] == pytest.approx(190 * 1.0005)
    assert position["stop"] == 184.91 and position["target"] == 207.05
    assert position["planned_stop_pct"] == pytest.approx((position["entry_price"] - 184.91) / position["entry_price"])
    assert position["planned_rr"] == pytest.approx((207.05 - position["entry_price"]) / (position["entry_price"] - 184.91))
    assert position["risk_plan"]["anchor_date"] == "2025-07-21"
    assert updated["config"] == account["config"]


def test_structure_without_previous_high_has_no_invented_target(source, monkeypatch):
    service, run = source
    run["run_id"] += "-without-target"
    run["results"][0]["report"]["target"] = None
    run["results"][0]["report"]["rr"] = None
    service.store.save_run(run)
    account = service.paper(run["run_id"], mode="forward")
    candle = {**bar(low=189, high=192, close=191), "open": 190}
    monkeypatch.setattr("multiedge.intraday.fetch_yahoo_intraday", lambda *a, **k: quotes(candle))
    service.monitor_paper(now=datetime.fromisoformat("2025-08-04T13:35:00+00:00"))
    position = service.store.get_paper(account["account_id"])["positions"][0]
    assert position["target"] is None and position["planned_rr"] is None
    assert position["stop"] == 184.91


def test_retired_example_account_is_read_only_and_excluded_from_monitor(source, monkeypatch):
    service, run = source
    account = service.paper(run["run_id"], mode="forward", exit_mode="fixed_percent", stop_loss_pct=.03, take_profit_pct=.25)
    account.update(automation_disabled=True, legacy_reason="旧视频百分比示例，保留历史")
    service.store.save_paper(account)
    def unexpected(*a, **k):
        raise AssertionError("Retired examples must not fetch or process prices")
    monkeypatch.setattr("multiedge.intraday.fetch_yahoo_intraday", unexpected)
    result = service.monitor_paper(now=datetime.fromisoformat("2025-08-04T13:35:00+00:00"))
    assert result["status"] == "no_active_orders"
    with pytest.raises(ValueError, match="停用"):
        service.paper(run["run_id"], **account["config"])
    assert service.store.get_paper(account["account_id"])["state"] == account["state"]


def test_equivalent_web_risk_budget_reuses_default_structural_account(source):
    service, run = source
    first = service.paper(run["run_id"], mode="forward")
    second = service.paper(run["run_id"], mode="forward", risk_fraction=.35 / 100)
    assert second["account_id"] == first["account_id"]
    assert len(service.store.papers()) == 1


def test_resynced_report_does_not_rewrite_registered_structure(source):
    service, run = source
    first = service.paper(run["run_id"], mode="forward")
    run["run_id"] += "-resynced"
    run["results"][0]["report"].update(stop=180, target=220)
    service.store.save_run(run)
    second = service.paper(run["run_id"], mode="forward")
    assert second["account_id"] == first["account_id"]
    assert second["orders"] == first["orders"]
    assert second["orders"][0]["risk_plan"]["source_run_id"] == first["run_id"]
