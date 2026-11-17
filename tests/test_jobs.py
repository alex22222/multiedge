"""日任务推进全部已登记账户，保留各自的资金与费用配置。"""
import json
from pathlib import Path

from multiedge.dashboard import ResearchService
from multiedge.jobs import daily, main
from multiedge.research import build_local_run


DATA = Path(__file__).resolve().parent.parent / "data"


def test_daily_advances_nondefault_baseline_paper_account(tmp_path, monkeypatch):
    monkeypatch.setattr(ResearchService, "monitor_paper", lambda *a, **k: {"status": "no_active_orders"})
    service = ResearchService(DATA, tmp_path)
    source = build_local_run(["GOOGL"], "2025-08-01", DATA)
    source["imported_at"] = "2025-08-01T22:00:00+00:00"
    service.store.save_run(source)
    monkeypatch.setattr("multiedge.dashboard.latest_complete_date", lambda: "2025-08-01")
    monkeypatch.setattr("multiedge.dashboard.utc_now", lambda: "2025-08-01T22:00:00+00:00")
    account = service.paper(source["run_id"], mode="forward", initial_cash=200000, execution_model="daily_ohlc")
    assert account["orders"][0]["status"] == "pending"
    universe = tmp_path / "universe.json"
    universe.write_text(json.dumps(["GOOGL"]))
    monkeypatch.setattr("multiedge.jobs.latest_complete_date", lambda: "2025-08-04")
    monkeypatch.setattr("multiedge.dashboard.latest_complete_date", lambda: "2025-08-04")
    monkeypatch.setattr("multiedge.dashboard.utc_now", lambda: "2025-08-04T22:00:00+00:00")
    monkeypatch.setattr("multiedge.research.utc_now", lambda: "2025-08-04T22:00:00+00:00")
    daily(DATA, tmp_path, universe, provider="marketwatch")
    advanced = service.store.get_paper(account["account_id"])
    assert advanced["as_of"] == "2025-08-04"
    assert advanced["orders"][0]["status"] == "filled"
    assert advanced["state"]["config"]["initial_cash"] == 200000


def test_daily_automatically_enrolls_buy_in_structure_and_never_reactivates_retired_examples(tmp_path, monkeypatch):
    reconciled = []
    def monitor(*args, **kwargs):
        reconciled.append(kwargs["reconcile"])
        return {"status": "completed"}
    monkeypatch.setattr(ResearchService, "monitor_paper", monitor)
    day = "2025-08-01"
    monkeypatch.setattr("multiedge.jobs.latest_complete_date", lambda: day)
    monkeypatch.setattr("multiedge.dashboard.latest_complete_date", lambda: day)
    monkeypatch.setattr("multiedge.dashboard.utc_now", lambda: "2025-08-01T22:00:00+00:00")
    monkeypatch.setattr("multiedge.research.utc_now", lambda: "2025-08-01T22:00:00+00:00")
    universe = tmp_path / "universe.json"
    universe.write_text(json.dumps(["GOOGL"]))
    service = ResearchService(DATA, tmp_path)
    source = service.store.save_run(build_local_run(["GOOGL"], day, DATA))
    example = service.paper(source["run_id"], mode="forward", exit_mode="fixed_percent", stop_loss_pct=.03, take_profit_pct=.25)
    example.update(automation_disabled=True, legacy_reason="旧百分比示例")
    service.store.save_paper(example)
    daily(DATA, tmp_path, universe, provider="marketwatch")
    accounts = service.store.papers()
    assert len(accounts) == 2
    active = [a for a in accounts if not a.get("automation_disabled")]
    assert len(active) == 1
    assert active[0]["exit_mode"] == "structural" and active[0]["execution_model"] == "intraday_5m"
    assert active[0]["orders"][0]["stop"] == 184.91
    assert active[0]["orders"][0]["target"] == 207.05
    assert service.store.get_paper(example["account_id"])["state"] == example["state"]
    daily(DATA, tmp_path, universe, provider="marketwatch")
    assert len(service.store.papers()) == 2
    assert all(len(a["orders"]) == 1 for a in service.store.papers())
    assert reconciled == [True, True]


def test_monitor_cli_preserves_partial_status_in_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(ResearchService, "monitor_paper", lambda *a, **k: {"status": "partial", "errors": {"SPX": "offline"}, "last_checked_at": "2026-11-17T15:00:00+00:00"})
    monkeypatch.setattr("sys.argv", ["multiedge.jobs", "monitor", "--state-dir", str(tmp_path)])
    main()
    receipt = json.loads((tmp_path / "last-paper-monitor.json").read_text())
    assert receipt["status"] == "partial" and receipt["errors"]["SPX"] == "offline"
    assert receipt["command"] == "monitor"
