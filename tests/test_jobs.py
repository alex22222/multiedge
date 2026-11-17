"""日任务推进全部已登记账户，保留各自的资金与费用配置。"""
import json
from pathlib import Path

from multiedge.dashboard import ResearchService
from multiedge.jobs import daily
from multiedge.research import build_local_run


DATA = Path(__file__).resolve().parent.parent / "data"


def test_daily_advances_nondefault_baseline_paper_account(tmp_path, monkeypatch):
    service = ResearchService(DATA, tmp_path)
    source = build_local_run(["GOOGL"], "2025-08-01", DATA)
    source["imported_at"] = "2025-08-01T22:00:00+00:00"
    service.store.save_run(source)
    monkeypatch.setattr("multiedge.dashboard.latest_complete_date", lambda: "2025-08-01")
    monkeypatch.setattr("multiedge.dashboard.utc_now", lambda: "2025-08-01T22:00:00+00:00")
    account = service.paper(source["run_id"], mode="forward", initial_cash=200000)
    assert account["orders"][0]["status"] == "pending"
    universe = tmp_path / "universe.json"
    universe.write_text(json.dumps(["GOOGL"]))
    monkeypatch.setattr("multiedge.jobs.latest_complete_date", lambda: "2025-08-04")
    monkeypatch.setattr("multiedge.dashboard.latest_complete_date", lambda: "2025-08-04")
    monkeypatch.setattr("multiedge.dashboard.utc_now", lambda: "2025-08-04T22:00:00+00:00")
    daily(DATA, tmp_path, universe, provider="marketwatch")
    advanced = service.store.get_paper(account["account_id"])
    assert advanced["as_of"] == "2025-08-04"
    assert advanced["orders"][0]["status"] == "filled"
    assert advanced["state"]["config"]["initial_cash"] == 200000
