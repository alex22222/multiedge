"""The daily-stock report is available through both read paths without writes."""
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from multiedge.app import make_handler
from multiedge.dashboard import ResearchService


def closed_account():
    return {"account_id": "paper-test", "execution_model": "intraday_5m", "mode": "forward",
            "last_checked_at": "2026-11-18T15:00:10+00:00", "data_as_of": "2026-11-18T15:00:00+00:00",
            "as_of": "2026-11-18", "cash": 100047.95, "equity": 100047.95,
            "positions": [], "orders": [], "equity_curve": [], "events": [],
            "trades": [{"ticker": "AMGN", "source_signal_id": "a", "entry_date": "2026-11-18",
                        "entry_timestamp": "2026-11-18T14:35:00+00:00", "entry_price": 100,
                        "quantity": 10, "entry_fee": 1, "exit_date": "2026-11-18",
                        "exit_timestamp": "2026-11-18T14:55:00+00:00", "exit_price": 105,
                        "exit_fee": 1.05, "net_pnl": 47.95}], "state": {"last_bar_at": {}}}


def test_presentation_report_preserves_underlying_ledger_and_other_accounts(tmp_path):
    service = ResearchService(state_dir=tmp_path)
    saved = service.store.save_paper(closed_account())
    first = service.paper_account(saved["account_id"])
    row = first["daily_pnl"]["rows"][0]
    assert row["ticker"] == "AMGN" and row["date"] == "2026-11-18"
    assert row["daily_pnl"] == pytest.approx(47.95)
    assert row["fees"] == pytest.approx(2.05)
    assert service.state()["paper_accounts"][0]["daily_pnl"] == first["daily_pnl"]
    assert service.paper_account(saved["account_id"]) == first
    assert service.store.get_paper(saved["account_id"]) == saved
    assert "daily_pnl" not in service.store.get_paper(saved["account_id"])


def test_http_paper_selection_includes_the_same_report_as_workspace_state(tmp_path):
    service = ResearchService(state_dir=tmp_path)
    service.store.save_paper(closed_account())
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state_dir=tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with urllib.request.urlopen(base + "/api/paper?id=paper-test") as response:
            selected = json.load(response)
        with urllib.request.urlopen(base + "/api/state") as response:
            workspace = json.load(response)
        assert selected["daily_pnl"]["rows"][0]["daily_pnl"] == pytest.approx(47.95)
        assert selected["daily_pnl"] == workspace["paper_accounts"][0]["daily_pnl"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
