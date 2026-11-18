"""New entry/structure policy owns a new account; old positions never migrate."""
import copy
from datetime import datetime
from pathlib import Path

import pytest

from multiedge.dashboard import ResearchService, _account_trailing_policy
from multiedge.research import build_local_run


DATA = Path(__file__).resolve().parent.parent / "data"
VERSION = "structural_retest_v2"


@pytest.fixture
def service_run(tmp_path, monkeypatch):
    service = ResearchService(DATA, tmp_path)
    run = build_local_run(["GOOGL"], "2025-08-01", DATA)
    run["imported_at"] = "2025-08-01T22:00:00+00:00"
    service.store.save_run(run)
    monkeypatch.setattr("multiedge.dashboard.utc_now", lambda: "2025-08-01T22:00:00+00:00")
    monkeypatch.setattr("multiedge.dashboard.latest_complete_date", lambda now=None: "2025-08-01")
    return service, run


def test_v2_creates_independent_frozen_account_and_preserves_legacy(service_run):
    service, run = service_run
    old = service.paper(run["run_id"], mode="forward")
    original = copy.deepcopy(old)
    new = service.paper(run["run_id"], mode="forward", strategy_version=VERSION)
    assert new["account_id"] != old["account_id"]
    assert new["strategy_version"] == new["state"]["config"]["strategy_version"] == VERSION
    assert new["trailing_policy"]["version"] == VERSION
    assert new["trailing_policy"]["enabled_at"] == new["started_at"]
    assert new["entry_policy"]["minimum_net_rr"] == 3
    plan = new["orders"][0]["risk_plan"]
    assert plan["strategy_version"] == VERSION and plan["support_band"]
    assert plan["anchor_date"] > "2025-07-21"
    assert plan["stop"] == new["orders"][0]["stop"]
    assert new["positions"] == [] and new["trades"] == []
    assert service.store.get_paper(old["account_id"]) == original
    again = service.paper(run["run_id"], mode="forward", strategy_version=VERSION)
    assert again["account_id"] == new["account_id"]
    assert again["orders"] == new["orders"] and again["trailing_policy"] == new["trailing_policy"]


def test_missing_target_is_audited_and_never_enters_v2(service_run):
    service, run = service_run
    run["run_id"] += "-no-target"
    run["results"][0]["report"]["target"] = None
    service.store.save_run(run)
    new = service.paper(run["run_id"], mode="forward", strategy_version=VERSION)
    assert new["orders"][0]["status"] == "skipped"
    assert new["orders"][0]["reason"] == "missing_target"
    view = service.paper_account(new["account_id"])
    assert len(view["entry_audits"]) == 1
    assert view["entry_audits"][0]["reason"] == "missing_target"
    assert new["positions"] == [] and new["trades"] == []


def test_v2_registered_plan_is_not_rewritten_by_a_resynced_report(service_run):
    service, run = service_run
    first = service.paper(run["run_id"], mode="forward", strategy_version=VERSION)
    run["run_id"] += "-resynced"
    run["results"][0]["report"].update(stop=100, target=300, mea_zone=[100, 101])
    service.store.save_run(run)
    second = service.paper(run["run_id"], mode="forward", strategy_version=VERSION)
    assert second["orders"] == first["orders"]
    assert second["orders"][0]["risk_plan"]["source_run_id"] == first["run_id"]


def test_existing_account_without_trailing_policy_is_never_upgraded_implicitly():
    account = {"positions": [{"ticker": "AMGN"}], "state": {"positions": [{"ticker": "AMGN"}]}}
    assert _account_trailing_policy(account, "2026-11-19T01:00:00+00:00") is None


@pytest.mark.parametrize("settings", [{"mode": "replay"}, {"mode": "forward", "execution_model": "daily_ohlc"},
                                      {"mode": "forward", "exit_mode": "fixed_percent", "stop_loss_pct": .03, "take_profit_pct": .25}])
def test_v2_rejects_incompatible_execution_modes(service_run, settings):
    service, run = service_run
    with pytest.raises(ValueError, match="v2|版本|盘中"):
        service.paper(run["run_id"], strategy_version=VERSION, **settings)


def test_unknown_strategy_version_is_rejected(service_run):
    service, run = service_run
    with pytest.raises(ValueError, match="版本"):
        service.paper(run["run_id"], mode="forward", strategy_version="made-up")


def test_v2_rejects_prices_that_do_not_match_the_frozen_signal_snapshot(service_run, monkeypatch):
    from multiedge.dashboard import load_prices
    service, run = service_run
    frame = load_prices("GOOGL", run["as_of"], DATA).copy()
    frame.iloc[-1, frame.columns.get_loc("Volume")] += 1
    monkeypatch.setattr("multiedge.dashboard.load_prices", lambda *a, **k: frame)
    account = service.paper(run["run_id"], mode="forward", strategy_version=VERSION)
    order = account["orders"][0]
    assert order["status"] == "skipped" and order["reason"] == "invalid_daily_plan"
    assert "快照" in order["risk_plan"]["data_error"]


def test_v2_rejects_daily_snapshot_received_after_the_plan_observation(service_run, monkeypatch):
    from multiedge.dashboard import load_prices
    service, run = service_run
    frame = load_prices("GOOGL", run["as_of"], DATA).copy()
    frame.attrs["retrieved_at"] = "2025-08-02T12:00:00+00:00"
    monkeypatch.setattr("multiedge.dashboard.load_prices", lambda *a, **k: frame)
    account = service.paper(run["run_id"], mode="forward", strategy_version=VERSION)
    order = account["orders"][0]
    assert order["status"] == "skipped" and order["reason"] == "invalid_daily_plan"
    assert "取得时间" in order["risk_plan"]["data_error"]


def test_split_after_signal_pauses_pending_v2_before_retest_or_entry(service_run, monkeypatch):
    from tests.test_monitor import bar, quotes
    service, run = service_run
    account = service.paper(run["run_id"], mode="forward", strategy_version=VERSION)
    data = quotes(bar())
    data["sources"]["GOOGL"]["splits"] = [{"date": "2025-08-04"}]
    monkeypatch.setattr("multiedge.intraday.fetch_yahoo_intraday", lambda *a, **k: data)
    receipt = service.monitor_paper(now=datetime.fromisoformat("2025-08-04T13:35:10+00:00"))
    updated = service.store.get_paper(account["account_id"])
    assert receipt["status"] == "partial"
    assert "拆股" in updated["monitor"]["errors"]["GOOGL"]
    assert updated["positions"] == [] and "entry_confirmation" not in updated["orders"][0]


def test_monitor_v2_confirms_then_enters_after_real_observation_and_keeps_own_policy(service_run, monkeypatch):
    from multiedge.dashboard import load_prices
    from tests.test_monitor import bar, quotes
    service, run = service_run
    account = service.paper(run["run_id"], mode="forward", strategy_version=VERSION)
    retest = {**bar(low=185.6, high=187.2, close=186.9), "open": 186}
    following = {**bar(minute="13:35", low=186.7, high=188, close=187.5), "open": 187}
    executable = {**bar(minute="13:40", low=187, high=188, close=187.6), "open": 187.2}
    monkeypatch.setattr("multiedge.intraday.fetch_yahoo_intraday", lambda *a, **k: quotes(retest))
    service.monitor_paper(now=datetime.fromisoformat("2025-08-04T13:35:10+00:00"))
    confirmed = service.store.get_paper(account["account_id"])
    assert confirmed["positions"] == [] and confirmed["orders"][0]["reason"] == "retest_confirmed"
    monkeypatch.setattr("multiedge.intraday.fetch_yahoo_intraday", lambda *a, **k: quotes(retest, following))
    service.monitor_paper(now=datetime.fromisoformat("2025-08-04T13:40:10+00:00"))
    assert service.store.get_paper(account["account_id"])["positions"] == []
    monkeypatch.setattr("multiedge.intraday.fetch_yahoo_intraday", lambda *a, **k: quotes(retest, following, executable))
    service.monitor_paper(now=datetime.fromisoformat("2025-08-04T13:45:10+00:00"))
    held = service.store.get_paper(account["account_id"])
    position = held["positions"][0]
    assert position["entry_timestamp"] == "2025-08-04T13:40:00+00:00"
    assert position["entry_checks"]["net_rr"] >= 3
    assert position["entry_checks"]["confirmation_observed_at"] < position["entry_timestamp"]
    assert held["entry_policy"] == account["entry_policy"] and held["trailing_policy"] == account["trailing_policy"]
    calls = []
    monkeypatch.setattr("multiedge.structure_v2.build_stop_update", lambda *a, **k: calls.append(k) or None)
    monkeypatch.setattr(service, "_trailing_frames", lambda *a, **k: ({ticker: load_prices(ticker, run["as_of"], DATA) for ticker in ("GOOGL", "SPX")}, {}))
    closing = {**bar(minute="13:45", low=184, high=187, close=185), "open": 185}
    monkeypatch.setattr("multiedge.intraday.fetch_yahoo_intraday", lambda *a, **k: quotes(retest, following, executable, closing))
    service.monitor_paper(now=datetime.fromisoformat("2025-08-04T13:50:10+00:00"))
    closed = service.store.get_paper(account["account_id"])
    assert calls and closed["positions"] == [] and len(closed["trades"]) == 1
    assert closed["trades"][0]["entry_checks"] == position["entry_checks"]
    assert closed["cash"] == pytest.approx(100000 + closed["trades"][0]["net_pnl"])
