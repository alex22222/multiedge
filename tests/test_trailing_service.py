"""Daily-confirmed trailing plans are executed prospectively by the 5m ledger."""
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from multiedge.data import load_daily
from tests.test_monitor import bar, quotes
from tests.test_structural_service import source


DATA = Path(__file__).resolve().parent.parent / "data"


def complete_session():
    start = datetime.fromisoformat("2025-08-04T13:30:00+00:00")
    return [{**bar(minute=(start + timedelta(minutes=5 * i)).strftime("%H:%M"), low=189, high=192, close=191), "open": 190} for i in range(78)]


def held(source, monkeypatch):
    service, run = source
    account = service.paper(run["run_id"], mode="forward")
    monkeypatch.setattr("multiedge.intraday.fetch_yahoo_intraday", lambda *a, **k: quotes(*complete_session()))
    service.monitor_paper(now=datetime.fromisoformat("2025-08-04T20:05:00+00:00"))
    return service, service.store.get_paper(account["account_id"])


def daily_frames():
    stock = load_daily(DATA / "GOOGL_2023-06-01_2025-08-08.csv").loc[:"2025-08-04"].copy()
    reference = load_daily(DATA / "SPX_2023-06-01_2025-08-08.csv").loc[:"2025-08-04"].copy()
    return {"GOOGL": stock, "SPX": reference}


def next_session(*minutes, opening=190):
    values = [{**bar(day="2025-08-05", minute=m, low=opening - 1, high=opening + 2, close=opening + 1), "open": opening} for m in minutes]
    data = quotes(*values)
    data["bars"]["SPX"] = [complete_session()[-1], *values]
    return data


def test_structural_account_enables_versioned_policy_but_fixed_experiment_does_not(source):
    service, run = source
    account = service.paper(run["run_id"], mode="forward")
    assert account["trailing_policy"]["version"] == "structure_ma20_hl_v1"
    assert account["trailing_policy"]["enabled_at"] == account["started_at"]
    fixed = service.paper(run["run_id"], mode="forward", exit_mode="fixed_percent", stop_loss_pct=.03, take_profit_pct=.25)
    assert "trailing_policy" not in fixed


def test_daily_evidence_is_queued_then_raised_on_a_future_bar_once(source, monkeypatch):
    service, first = held(source, monkeypatch)
    monkeypatch.setattr("multiedge.dashboard.latest_complete_date", lambda now=None: "2025-08-04")
    monkeypatch.setattr(service, "_trailing_frames", lambda *a, **k: (daily_frames(), {}))
    monkeypatch.setattr("multiedge.intraday.fetch_yahoo_intraday", lambda *a, **k: next_session("13:30"))
    service.monitor_paper(now=datetime.fromisoformat("2025-08-05T13:35:00+00:00"))
    queued = service.store.get_paper(first["account_id"])
    assert len(queued["stop_updates"]) == 1 and queued["stop_updates"][0]["status"] == "pending"
    assert queued["positions"][0]["stop"] == 184.91 and queued["stop_adjustments"] == []
    monkeypatch.setattr("multiedge.intraday.fetch_yahoo_intraday", lambda *a, **k: next_session("13:30", "13:35"))
    now = datetime.fromisoformat("2025-08-05T13:40:00+00:00")
    service.monitor_paper(now=now)
    result = service.store.get_paper(first["account_id"])
    position = result["positions"][0]
    assert position["initial_stop"] == 184.91 and position["stop"] > 184.91
    assert position["quantity"] == first["positions"][0]["quantity"]
    assert position["planned_rr"] == first["positions"][0]["planned_rr"]
    adjustment = result["stop_adjustments"][0]
    assert adjustment["effective_at"] == "2025-08-05T13:35:00+00:00"
    assert adjustment["source_as_of"] == "2025-08-04"
    assert adjustment["evidence"]["data_fingerprint"]
    service.monitor_paper(now=now)
    repeated = service.store.get_paper(first["account_id"])
    assert repeated["state"] == result["state"]


def test_daily_calculation_failure_does_not_disable_existing_protective_exit(source, monkeypatch):
    service, first = held(source, monkeypatch)
    def unavailable(*args, **kwargs):
        raise ValueError("daily snapshot unavailable")
    monkeypatch.setattr(service, "_trailing_frames", unavailable)
    monkeypatch.setattr("multiedge.intraday.fetch_yahoo_intraday", lambda *a, **k: next_session("13:30", opening=180))
    receipt = service.monitor_paper(now=datetime.fromisoformat("2025-08-05T13:35:00+00:00"))
    saved = service.store.get_paper(first["account_id"])
    assert saved["positions"] == [] and saved["trades"][0]["exit_reason"] == "stop_gap"
    assert saved["trades"][0]["initial_stop"] == 184.91
    assert saved["monitor"]["trailing_errors"] and receipt["status"] == "partial"


def test_held_stock_is_managed_even_if_not_in_latest_buy_list(source, monkeypatch):
    service, first = held(source, monkeypatch)
    calls = []
    def frames(tickers, provider, now):
        calls.append(set(tickers))
        return daily_frames(), {}
    monkeypatch.setattr(service, "_trailing_frames", frames)
    monkeypatch.setattr("multiedge.dashboard.latest_complete_date", lambda now=None: "2025-08-04")
    monkeypatch.setattr("multiedge.intraday.fetch_yahoo_intraday", lambda *a, **k: next_session("13:30"))
    run = service.store.get_run(first["run_id"])
    run["run_id"] += "-no-buy"
    run["results"][0]["verdict"] = "NO"
    service.store.save_run(run)
    service.monitor_paper(now=datetime.fromisoformat("2025-08-05T13:35:00+00:00"))
    assert calls == [{"GOOGL"}]
    assert len(service.store.get_paper(first["account_id"])["stop_updates"]) == 1


def test_future_retrieval_receipt_is_not_used_for_an_earlier_decision(source, monkeypatch):
    service, first = held(source, monkeypatch)
    frames = daily_frames()
    frames["GOOGL"].attrs["snapshots"] = [{"retrieved_at": "2025-08-06T12:00:00+00:00"}]
    monkeypatch.setattr(service, "_trailing_frames", lambda *a: (frames, {}))
    monkeypatch.setattr("multiedge.dashboard.latest_complete_date", lambda now=None: "2025-08-04")
    monkeypatch.setattr("multiedge.intraday.fetch_yahoo_intraday", lambda *a, **k: next_session("13:30"))
    service.monitor_paper(now=datetime.fromisoformat("2025-08-05T13:35:00+00:00"))
    saved = service.store.get_paper(first["account_id"])
    assert saved["stop_updates"] == [] and saved["monitor"]["trailing_errors"]
    assert saved["positions"][0]["stop"] == 184.91


def test_daily_source_must_cover_latest_completed_session_seen_in_real_bars(source, monkeypatch):
    service, first = held(source, monkeypatch)
    frames = {t: f.loc[:"2025-08-01"].copy() for t, f in daily_frames().items()}
    monkeypatch.setattr(service, "_trailing_frames", lambda *a: (frames, {}))
    monkeypatch.setattr("multiedge.dashboard.latest_complete_date", lambda now=None: "2025-08-04")
    monkeypatch.setattr("multiedge.intraday.fetch_yahoo_intraday", lambda *a, **k: next_session("13:30"))
    service.monitor_paper(now=datetime.fromisoformat("2025-08-05T13:35:00+00:00"))
    saved = service.store.get_paper(first["account_id"])
    assert saved["monitor"]["trailing_errors"]
    assert "2025-08-04" in str(saved["monitor"]["trailing_errors"])
