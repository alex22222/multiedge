"""来源完整性、配置版本和过期数据须能明确追溯。"""
import json
from pathlib import Path

import pytest

from multiedge.research import ResearchStore, build_local_run, build_multica_run
from multiedge.dashboard import ResearchService
from multiedge.data import load_daily


DATA = Path(__file__).resolve().parent.parent / "data"


def test_partial_multica_import_does_not_invent_missing_results(tmp_path):
    (tmp_path / "screen_json").mkdir()
    (tmp_path / "issue.json").write_text(json.dumps({"identifier": "SATU-107", "status": "in_review"}))
    (tmp_path / "manifest.json").write_text(json.dumps({"source_issue_url": "https://multica.ai/task", "expected_universe_count": 2}))
    (tmp_path / "screen_universe.execution-snapshot.json").write_text(json.dumps({"as_of": "2026-11-16", "tickers": [["AMGN", "Amgen", "Health Care"], ["MMM", "3M", "Industrials"]]}))
    report = {"ticker": "AMGN", "as_of": "2026-11-16", "verdict": "BUY", "close": 462.32, "stop": 412.95, "target": None, "edges": {}, "checklist": []}
    (tmp_path / "screen_json" / "AMGN.json").write_text(json.dumps(report))
    run = build_multica_run(tmp_path)
    assert run["coverage"] == "partial"
    assert run["expected_count"] == 2 and run["result_count"] == 1
    assert run["counts"] == {"BUY": 1, "WATCH": 0, "NO": 0, "ERROR": 0, "MISSING": 1}
    assert run["results"][1]["verdict"] == "MISSING"
    assert run["results"][0]["layers"]["cognitive"]["status"] == "unverified"
    assert run["status"] == "in_review"


def test_store_versions_and_run_immutability(tmp_path):
    store = ResearchStore(tmp_path)
    run = {"run_id": "r1", "as_of": "2025-08-01", "results": []}
    store.save_run(run)
    store.save_run(run)
    with pytest.raises(ValueError, match="不可覆盖"):
        store.save_run({**run, "as_of": "2025-08-02"})
    first = store.save_profile({"name": "我的RSI", "rsi_enabled": True})
    second = store.save_profile({"name": "我的RSI", "rsi_enabled": True, "rsi_min": 45})
    assert first["id"] != second["id"]
    assert len(store.profiles()) == 2


def test_cached_local_run_preserves_google_baseline_and_failures():
    run = build_local_run(["GOOGL", "MISSING"], "2025-08-01", DATA)
    assert run["results"][0]["report"]["verdict"] == "BUY"
    assert run["results"][0]["report"]["rr"] == 4.25
    assert run["results"][1]["verdict"] == "ERROR"
    assert run["counts"]["ERROR"] == 1
    assert run["data_snapshot"]["price_basis"] == "unadjusted"


def test_future_prices_do_not_leak_into_local_run():
    run = build_local_run(["GOOGL"], "2025-06-30", DATA)
    assert run["results"][0]["report"]["as_of"] == "2025-06-30"
    assert run["results"][0]["report"]["verdict"] == "NO"
    assert run["data_snapshot"]["ranges"]["GOOGL"]["end"] == "2025-06-30"


def test_distinct_provenance_has_distinct_run_identity(tmp_path):
    store = ResearchStore(tmp_path)
    a = build_local_run(["GOOGL"], "2025-08-01", DATA, source="验收")
    b = build_local_run(["GOOGL"], "2025-08-02", DATA, source="周末回看")
    assert a["run_id"] != b["run_id"]
    store.save_run(a)
    store.save_run(b)


def test_new_forward_account_cannot_fill_old_saved_signal(tmp_path, monkeypatch):
    service = ResearchService(DATA, tmp_path)
    run = build_local_run(["GOOGL"], "2025-08-01", DATA)
    run["imported_at"] = "2025-08-01T22:00:00+00:00"
    service.store.save_run(run)
    monkeypatch.setattr("multiedge.dashboard.utc_now", lambda: "2025-08-08T22:00:00+00:00")
    result = service.paper(run["run_id"], mode="forward")
    assert result["positions"] == [] and result["trades"] == []
    assert result["orders"][0]["reason"] == "signal_observed_after_entry"
    again = service.paper(run["run_id"], mode="forward")
    assert again["orders"] == result["orders"]


def test_forward_rejects_future_imported_signal(tmp_path):
    service = ResearchService(DATA, tmp_path)
    run = build_local_run(["GOOGL"], "2025-08-01", DATA)
    run["as_of"] = "2099-01-01"
    service.store.save_run(run)
    with pytest.raises(ValueError, match="完整交易日"):
        service.paper(run["run_id"], mode="forward")


def test_new_evidence_preserves_previous_sources(tmp_path):
    store = ResearchStore(tmp_path)
    run = build_local_run(["GOOGL"], "2025-08-01", DATA)
    store.save_run(run)
    store.save_evidence(run["run_id"], "GOOGL", "cognitive", {"summary": "首份公告待核验", "source_url": "https://example.com/first"})
    store.save_evidence(run["run_id"], "GOOGL", "cognitive", {"summary": "第二份公告待核验", "source_url": "https://example.com/second"})
    evidence = store.get_run(run["run_id"])["results"][0]["layers"]["cognitive"]["evidence"]
    assert [e["source_url"] for e in evidence] == ["https://example.com/first", "https://example.com/second"]


def test_new_empty_forward_account_has_no_pre_registration_equity_history(tmp_path, monkeypatch):
    service = ResearchService(DATA, tmp_path)
    run = build_local_run(["GOOGL"], "2025-06-30", DATA)
    service.store.save_run(run)
    monkeypatch.setattr("multiedge.dashboard.utc_now", lambda: "2026-11-18T01:00:00+00:00")
    result = service.paper(run["run_id"], mode="forward")
    assert result["orders"] == [] and result["equity_curve"] == []


def test_history_lists_metadata_and_loads_full_report_only_on_selection(tmp_path):
    store = ResearchStore(tmp_path)
    run = store.save_run(build_local_run(["GOOGL"], "2025-08-01", DATA))
    assert "results" not in store.runs()[0]
    assert store.get_run(run["run_id"])["results"][0]["report"]["chart"]["dates"]


def test_replay_new_price_snapshot_recomputes_history_in_new_account(tmp_path, monkeypatch):
    service = ResearchService(DATA, tmp_path)
    run = service.store.save_run(build_local_run(["GOOGL"], "2025-08-01", DATA))
    googl = load_daily(DATA / "GOOGL_2023-06-01_2025-08-08.csv").loc["2025-08-01":]
    spx = load_daily(DATA / "SPX_2023-06-01_2025-08-08.csv").loc["2025-08-01":]
    monkeypatch.setattr("multiedge.dashboard.latest_complete_date", lambda: "2025-08-08")
    monkeypatch.setattr("multiedge.dashboard.cached_frames", lambda *a: {"GOOGL": googl.drop("2025-08-04"), "SPX": spx})
    first = service.paper(run["run_id"], mode="replay")
    assert first["orders"][0]["reason"] == "missing_entry_bar"
    monkeypatch.setattr("multiedge.dashboard.cached_frames", lambda *a: {"GOOGL": googl, "SPX": spx})
    second = service.paper(run["run_id"], mode="replay")
    assert second["account_id"] != first["account_id"]
    assert second["orders"][0]["status"] == "filled"


def test_fixed_percent_replay_uses_actual_entry_price_and_versions_exit_plan(tmp_path):
    service = ResearchService(DATA, tmp_path)
    run = service.store.save_run(build_local_run(["GOOGL"], "2025-08-01", DATA))
    three = service.paper(run["run_id"], mode="replay", exit_mode="fixed_percent", stop_loss_pct=.03, take_profit_pct=.25)
    five = service.paper(run["run_id"], mode="replay", exit_mode="fixed_percent", stop_loss_pct=.05, take_profit_pct=.25)
    assert three["account_id"] != five["account_id"]
    assert three["config"]["stop_loss_pct"] == .03
    assert five["config"]["stop_loss_pct"] == .05
    position = (three["positions"] + three["trades"])[0]
    assert position["stop"] == pytest.approx(position["entry_price"] * .97)
    assert position["target"] == pytest.approx(position["entry_price"] * 1.25)
    assert position["planned_rr"] == pytest.approx(25 / 3)
