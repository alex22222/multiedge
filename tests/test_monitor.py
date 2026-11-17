"""Service integration: queued BUYs, price checks, and persisted Paper accounting."""
from datetime import datetime
from pathlib import Path

import pytest

from multiedge.dashboard import ResearchService
from multiedge.research import build_local_run


DATA = Path(__file__).resolve().parent.parent / "data"


def bar(day="2025-08-04", minute="13:30", low=99, high=102, close=101):
    return {"timestamp": f"{day}T{minute}:00+00:00", "open": 100, "high": high,
            "low": low, "close": close, "volume": 1000,
            "session_start": f"{day}T13:30:00+00:00", "session_end": f"{day}T20:00:00+00:00"}


@pytest.fixture
def queued(tmp_path, monkeypatch):
    service = ResearchService(DATA, tmp_path)
    run = build_local_run(["GOOGL"], "2025-08-01", DATA)
    run["imported_at"] = "2025-08-01T22:00:00+00:00"
    service.store.save_run(run)
    monkeypatch.setattr("multiedge.dashboard.latest_complete_date", lambda: "2025-08-01")
    monkeypatch.setattr("multiedge.dashboard.utc_now", lambda: "2025-08-01T22:00:00+00:00")
    account = service.paper(run["run_id"], mode="forward", exit_mode="fixed_percent", stop_loss_pct=.03, take_profit_pct=.25)
    return service, account


def quotes(*stock):
    return {"bars": {"GOOGL": list(stock), "SPX": [bar(day="2025-08-01", minute="19:55"), *stock]},
            "sources": {"GOOGL": {"stale": False, "splits": []}, "SPX": {"stale": False}}, "errors": {}}


def test_monitor_persists_entry_then_automatic_stop_and_does_not_repeat(queued, monkeypatch):
    service, account = queued
    monkeypatch.setattr("multiedge.intraday.fetch_yahoo_intraday", lambda *a, **k: quotes(bar()))
    receipt = service.monitor_paper(now=datetime.fromisoformat("2025-08-04T13:35:00+00:00"))
    assert receipt["status"] == "completed"
    filled = service.store.get_paper(account["account_id"])
    assert filled["positions"][0]["entry_price"] == pytest.approx(100.05)
    assert filled["positions"][0]["stop"] == pytest.approx(100.05 * .97)
    assert filled["positions"][0]["source_run_id"] == account["run_id"]
    assert filled["config"] == account["config"]
    monkeypatch.setattr("multiedge.intraday.fetch_yahoo_intraday", lambda *a, **k: quotes(bar(), bar(minute="13:35", low=96)))
    moment = datetime.fromisoformat("2025-08-04T13:40:00+00:00")
    service.monitor_paper(now=moment)
    closed = service.store.get_paper(account["account_id"])
    assert closed["positions"] == [] and len(closed["trades"]) == 1
    assert closed["trades"][0]["exit_reason"] == "stop"
    assert closed["cash"] == pytest.approx(100000 + closed["trades"][0]["net_pnl"])
    assert service.monitor_paper(now=moment)["status"] == "no_active_orders"
    assert service.store.get_paper(account["account_id"])["state"] == closed["state"]


def test_stale_provider_quotes_are_visible_and_never_fill(queued, monkeypatch):
    service, account = queued
    data = quotes()
    data["sources"]["GOOGL"]["stale"] = True
    monkeypatch.setattr("multiedge.intraday.fetch_yahoo_intraday", lambda *a, **k: data)
    receipt = service.monitor_paper(now=datetime.fromisoformat("2025-08-04T13:40:00+00:00"))
    assert receipt["status"] == "partial"
    saved = service.store.get_paper(account["account_id"])
    assert saved["data_stale"] and saved["monitor"]["status"] == "data_error"
    assert saved["orders"][0]["status"] == "pending" and saved["positions"] == []


def test_weekend_monitor_does_not_fetch_or_change_ledger(queued, monkeypatch):
    service, account = queued
    def unexpected(*a, **k):
        raise AssertionError("market-closed check fetched network data")
    monkeypatch.setattr("multiedge.intraday.fetch_yahoo_intraday", unexpected)
    assert service.monitor_paper(now=datetime.fromisoformat("2025-08-02T14:40:00+00:00"))["status"] == "market_closed"
    assert service.store.get_paper(account["account_id"])["state"] == account["state"]


def test_split_during_holding_pauses_instead_of_creating_false_stop(queued, monkeypatch):
    service, account = queued
    monkeypatch.setattr("multiedge.intraday.fetch_yahoo_intraday", lambda *a, **k: quotes(bar()))
    service.monitor_paper(now=datetime.fromisoformat("2025-08-04T13:35:00+00:00"))
    data = quotes(bar(day="2025-08-05", low=49, high=100, close=50))
    data["sources"]["GOOGL"]["splits"] = [{"date": "2025-08-05"}]
    monkeypatch.setattr("multiedge.intraday.fetch_yahoo_intraday", lambda *a, **k: data)
    assert service.monitor_paper(now=datetime.fromisoformat("2025-08-05T13:35:00+00:00"))["status"] == "partial"
    saved = service.store.get_paper(account["account_id"])
    assert len(saved["positions"]) == 1 and saved["trades"] == []
    assert "拆股" in saved["monitor"]["errors"]["GOOGL"]


def test_concurrent_enqueue_after_fetch_cutoff_is_not_rewound(queued, monkeypatch):
    service, account = queued
    def fetch(*args, **kwargs):
        monkeypatch.setattr("multiedge.dashboard.utc_now", lambda: "2025-08-04T13:35:01+00:00")
        service.paper(account["run_id"], **account["config"])
        return quotes(bar())
    monkeypatch.setattr("multiedge.intraday.fetch_yahoo_intraday", fetch)
    result = service.monitor_paper(now=datetime.fromisoformat("2025-08-04T13:35:00+00:00"))
    assert result["accounts"][0]["status"] == "superseded"
    saved = service.store.get_paper(account["account_id"])
    assert saved["state"]["last_checked_at"] == "2025-08-04T13:35:01+00:00"
    assert saved["positions"] == []
