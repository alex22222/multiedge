"""Independent integration regressions for source integrity before trailing."""
from datetime import datetime
from pathlib import Path

from multiedge.data import load_daily
from tests.test_monitor import bar
from tests.test_structural_service import source
from tests.test_trailing_service import complete_session, daily_frames, held, next_session


DATA = Path(__file__).resolve().parent.parent / "data"


def frames_through_august_fifth():
    return {ticker: load_daily(DATA / f"{ticker}_2023-06-01_2025-08-08.csv").loc[:"2025-08-05"].copy()
            for ticker in ("GOOGL", "SPX")}


def test_known_daily_split_pauses_price_accounting_even_if_intraday_events_omit_it(source, monkeypatch):
    service, first = held(source, monkeypatch)
    daily = frames_through_august_fifth()
    daily["GOOGL"].attrs["splits"] = [{"date": "2025-08-05", "numerator": 2, "denominator": 1}]
    monkeypatch.setattr(service, "_trailing_frames", lambda *a, **k: (daily, {}))
    values = []
    for original in complete_session():
        value = {**original, "open": 100, "high": 102, "low": 99, "close": 101}
        for field in ("timestamp", "session_start", "session_end"):
            value[field] = value[field].replace("2025-08-04", "2025-08-05")
        values.append(value)
    values.append(bar(day="2025-08-06", minute="13:30"))
    # The daily source proves a corporate action even if a separate 5m source
    # omits it; comparing the old absolute stop with split-price bars is invalid.
    quotes = {"bars": {"GOOGL": values, "SPX": [complete_session()[-1], *values]},
              "sources": {"GOOGL": {"stale": False, "splits": []}, "SPX": {"stale": False}}, "errors": {}}
    monkeypatch.setattr("multiedge.intraday.fetch_yahoo_intraday", lambda *a, **k: quotes)
    receipt = service.monitor_paper(now=datetime.fromisoformat("2025-08-06T13:35:00+00:00"))
    saved = service.store.get_paper(first["account_id"])
    assert receipt["status"] == "partial"
    assert len(saved["positions"]) == 1 and saved["trades"] == []
    assert "GOOGL" in saved["monitor"]["errors"]
    assert saved["positions"][0]["stop"] == 184.91


def test_internal_missing_daily_session_is_not_accepted_as_complete_ma_history(source, monkeypatch):
    service, _ = source
    daily = frames_through_august_fifth()
    daily["GOOGL"] = daily["GOOGL"].drop("2025-08-04")
    monkeypatch.setattr("multiedge.dashboard.latest_complete_date", lambda now=None: "2025-08-05")
    monkeypatch.setattr("multiedge.dashboard.load_prices", lambda ticker, *a, **k: daily[ticker])
    frames, errors = service._trailing_frames(["GOOGL"], "marketwatch", datetime.fromisoformat("2025-08-06T13:35:00+00:00"))
    assert "GOOGL" in errors
    assert "GOOGL" not in frames
    assert "SPX" in frames


def test_malformed_daily_split_metadata_reports_trailing_error_without_blocking_old_stop(source, monkeypatch):
    service, first = held(source, monkeypatch)
    daily = daily_frames()
    daily["GOOGL"].attrs["splits"] = None
    monkeypatch.setattr(service, "_trailing_frames", lambda *a, **k: (daily, {}))
    monkeypatch.setattr("multiedge.dashboard.latest_complete_date", lambda now=None: "2025-08-04")
    monkeypatch.setattr("multiedge.intraday.fetch_yahoo_intraday", lambda *a, **k: next_session("13:30", opening=180))
    receipt = service.monitor_paper(now=datetime.fromisoformat("2025-08-05T13:35:00+00:00"))
    saved = service.store.get_paper(first["account_id"])
    assert receipt["status"] == "partial"
    assert saved["positions"] == []
    assert saved["trades"][0]["exit_reason"] == "stop_gap"
    assert "GOOGL" in saved["monitor"]["trailing_errors"]
