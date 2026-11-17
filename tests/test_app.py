"""真实 HTTP 路径验证，包含部署前缀、输入校验与原始快照报告。"""
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from multiedge.app import make_handler
from multiedge.research import ResearchStore, build_local_run


DATA = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture
def web(tmp_path):
    store = ResearchStore(tmp_path)
    run = build_local_run(["GOOGL"], "2025-08-01", DATA)
    store.save_run(run)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(str(DATA), state_dir=str(tmp_path)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", run["run_id"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_state_static_assets_and_saved_report(web):
    base, run_id = web
    with urllib.request.urlopen(base + "/api/state") as response:
        state = json.load(response)
    assert state["active_run"]["run_id"] == run_id
    with urllib.request.urlopen(base + "/") as response:
        html = response.read().decode()
    assert "dashboard.js" in html and 'src="/' not in html
    with urllib.request.urlopen(base + f"/report?run_id={run_id}&ticker=GOOGL") as response:
        report = response.read().decode()
    assert "BUY" in report and "184.91" in report and "4.25R" in report


def test_profile_versions_and_cross_origin_rejection(web):
    base, _ = web
    payload = json.dumps({"name": "RSI 实验", "rsi_enabled": True}).encode()
    req = urllib.request.Request(base + "/api/profile", data=payload, headers={"Content-Type": "application/json", "Origin": "https://attacker.invalid"})
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(req)
    assert err.value.code == 403
    req = urllib.request.Request(base + "/api/profile", data=payload, headers={"Content-Type": "application/json", "Origin": base})
    with urllib.request.urlopen(req) as response:
        profile = json.load(response)
    assert profile["rsi_enabled"] is True and profile["id"]


def test_malformed_symbol_rejected_without_network(web):
    base, _ = web
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(base + "/report?ticker=../../private&as_of=2025-08-01")
    assert err.value.code == 400


def test_null_evidence_returns_structured_validation_error(web):
    base, run_id = web
    req = urllib.request.Request(base + "/api/evidence", data=json.dumps({"run_id": run_id, "ticker": "GOOGL", "layer": "cognitive", "summary": None, "source_url": "https://example.com"}).encode(), headers={"Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(req)
    assert err.value.code == 400 and "error" in json.load(err.value)
