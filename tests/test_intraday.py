"""Forward 5-minute Paper uses registered rules and actual completed bars."""
import copy
import json
from datetime import datetime, timezone
from urllib.error import URLError
from unittest.mock import patch

import pytest

from multiedge.intraday import apply_intraday, fetch_yahoo_intraday


CONFIG = {"initial_cash": 100000, "risk_fraction": .0035, "fee_bps": 0,
          "slippage_bps": 0, "stop_loss_pct": .03, "take_profit_pct": .25}


def bar(day="2026-11-17", minute="14:35", o=100, h=102, low=99, close=101):
    return {"timestamp": f"{day}T{minute}:00+00:00", "open": o, "high": h,
            "low": low, "close": close, "volume": 1000,
            "session_start": f"{day}T14:30:00+00:00", "session_end": f"{day}T21:00:00+00:00"}


def signal(**values):
    return {"ticker": "ABC", "signal_date": "2026-11-16", "signal_id": "s1",
            "observed_at": "2026-11-17T14:32:00+00:00", **values}


def frames(*bars):
    return {"SPX": [bar(day="2026-11-16", minute="20:55"), *bars], "ABC": list(bars)}


def run(signals, data, now="2026-11-17T14:45:00+00:00", **kwargs):
    kwargs.setdefault("started_at", "2026-11-17T14:32:00+00:00")
    return apply_intraday(signals, data, now=now, config=CONFIG, **kwargs)


def test_queue_without_calendar_does_not_fabricate_trade_or_old_equity():
    result = run([signal()], {}, started_at="2026-11-17T14:32:00+00:00")
    assert result["orders"][0]["status"] == "pending"
    assert result["orders"][0]["reason"] == "calendar_unavailable"
    assert result["positions"] == result["trades"] == result["equity_curve"] == []


def test_first_post_registration_bar_sets_actual_entry_and_fixed_plan():
    data = frames(bar(minute="14:30", o=90, h=93, low=89, close=92), bar(o=101, h=103, low=100, close=102))
    result = run([signal()], data, started_at="2026-11-17T14:32:00+00:00")
    p = result["positions"][0]
    assert p["entry_timestamp"] == "2026-11-17T14:35:00+00:00"
    assert p["entry_price"] == 101
    assert p["stop"] == pytest.approx(97.97)
    assert p["target"] == pytest.approx(126.25)
    assert p["planned_rr"] == pytest.approx(25 / 3)
    assert all(point["date"] >= "2026-11-17T14:32:00+00:00" for point in result["equity_curve"])


def test_five_percent_plan_has_five_r_target():
    result = apply_intraday([signal()], frames(bar()), now="2026-11-17T14:45:00+00:00",
                            config={**CONFIG, "stop_loss_pct": .05}, started_at="2026-11-17T14:32:00+00:00")
    assert result["positions"][0]["stop"] == 95
    assert result["positions"][0]["target"] == 125
    assert result["positions"][0]["planned_rr"] == 5


def test_current_signal_day_and_future_incomplete_bars_cannot_fill():
    data = frames(bar(minute="14:40"))
    same_day = run([signal(signal_date="2026-11-17")], data, now="2026-11-17T14:45:00+00:00")
    assert not same_day["positions"]
    incomplete = run([signal()], data, now="2026-11-17T14:42:00+00:00")
    assert not incomplete["positions"]


def test_old_signal_outside_available_calendar_is_stale():
    result = run([signal(signal_date="2025-08-01")], frames(bar()))
    assert result["orders"][0]["status"] == "skipped"
    assert result["orders"][0]["reason"] == "stale_signal"


def test_next_session_entry_window_expires_instead_of_buying_days_later():
    data = {"SPX": [bar(day="2026-11-16"), bar(), bar(day="2026-11-18")], "ABC": [bar(day="2026-11-18")]}
    result = run([signal()], data, now="2026-11-18T14:45:00+00:00")
    assert result["orders"][0]["reason"] == "signal_expired"
    assert not result["positions"]


def test_target_touch_is_automatic_and_retains_price_evidence():
    result = run([signal()], frames(bar(h=126, low=99, close=124)))
    assert not result["positions"] and len(result["trades"]) == 1
    trade = result["trades"][0]
    assert trade["exit_price"] == 125 and trade["exit_reason"] == "target"
    assert trade["price_bar"]["high"] == 126
    assert trade["net_pnl"] > 0


def test_dual_touch_is_stop_first_and_gap_stop_uses_actual_open():
    both = run([signal()], frames(bar(h=126, low=96, close=110)))
    assert both["trades"][0]["exit_price"] == 97
    assert both["trades"][0]["exit_reason"] == "stop_and_target"
    first = run([signal()], frames(bar()), now="2026-11-17T14:40:00+00:00")
    gap = run([], frames(bar(minute="14:40", o=90, h=93, low=89, close=92)), state=first["state"])
    assert gap["trades"][0]["exit_price"] == 90
    assert gap["trades"][0]["exit_reason"] == "stop_gap"


def test_no_spx_prevents_new_entry_but_does_not_disable_protective_exit():
    first = run([signal()], frames(bar()), now="2026-11-17T14:40:00+00:00")
    result = run([], {"ABC": [bar(minute="14:40", h=101, low=96, close=98)]}, state=first)
    assert result["trades"][0]["exit_reason"] == "stop"
    queued = run([signal()], {"ABC": [bar()]})
    assert not queued["positions"] and queued["orders"][0]["reason"] == "calendar_unavailable"


def test_resume_is_idempotent_and_does_not_mutate_saved_state():
    first = run([signal()], frames(bar()), now="2026-11-17T14:40:00+00:00")
    saved = copy.deepcopy(first["state"])
    updated = run([signal()], frames(bar(), bar(minute="14:40", h=126, low=100, close=124)), state=saved)
    assert saved == first["state"]
    repeated = run([signal()], frames(bar(), bar(minute="14:40", h=126, low=100, close=124)), state=updated)
    assert repeated["state"] == updated["state"]
    assert len(repeated["trades"]) == 1


def test_missing_stock_quotes_retain_old_mark_without_new_fake_value():
    first = run([signal()], frames(bar()), now="2026-11-17T14:40:00+00:00")
    updated = run([], {"SPX": [bar(day="2026-11-16", minute="20:55"), bar(minute="14:40")]}, state=first)
    assert updated["positions"][0]["mark_timestamp"] == "2026-11-17T14:40:00+00:00"
    assert updated["positions"][0]["stale"]
    assert updated["metrics"]["stale_positions"] == 1


def test_accounting_fees_slippage_and_cash_reconcile():
    result = apply_intraday([signal()], frames(bar(h=130, low=99, close=126)), now="2026-11-17T14:45:00+00:00",
                            config={**CONFIG, "fee_bps": 5, "slippage_bps": 5}, started_at="2026-11-17T14:32:00+00:00")
    t = result["trades"][0]
    assert t["entry_price"] == pytest.approx(100.05)
    assert t["target"] == pytest.approx(125.0625)
    assert result["equity"] == pytest.approx(CONFIG["initial_cash"] + t["net_pnl"])
    assert result["cash"] >= 0


def test_extreme_entry_slippage_cannot_create_a_stop_above_observed_open():
    result = apply_intraday([signal()], frames(bar()), now="2026-11-17T14:45:00+00:00",
                            config={**CONFIG, "slippage_bps": 1000}, started_at="2026-11-17T14:32:00+00:00")
    assert result["orders"][0]["reason"] == "invalid_stop"
    assert not result["positions"] and not result["trades"]


def test_resume_rejects_parameter_or_signal_identity_changes():
    first = run([signal()], frames(bar()), now="2026-11-17T14:40:00+00:00")
    with pytest.raises(ValueError, match="configuration"):
        apply_intraday([], {}, now="2026-11-17T14:45:00+00:00", config={**CONFIG, "stop_loss_pct": .05}, state=first)
    with pytest.raises(ValueError, match="signal_id"):
        run([signal(signal_date="2026-11-13")], frames(bar()), state=first)


def test_same_signal_from_resynced_source_does_not_duplicate_or_rewrite_order():
    first = run([signal(source_run_id="original")], frames(bar()), now="2026-11-17T14:40:00+00:00")
    second = run([signal(source_run_id="resynced")], frames(bar()), now="2026-11-17T14:40:00+00:00", state=first)
    assert second["orders"] == first["orders"]
    assert len(second["positions"]) == 1


def test_missing_new_data_marks_old_position_stale_without_new_equity_point():
    first = run([signal()], frames(bar()), now="2026-11-17T14:40:00+00:00")
    second = run([], {}, now="2026-11-17T15:00:00+00:00", state=first)
    assert second["data_stale"] and second["positions"][0]["stale"]
    assert second["equity_curve"] == first["equity_curve"]


def test_held_position_gap_pauses_until_missing_bars_are_reconciled():
    first = run([signal()], frames(bar()), now="2026-11-17T14:40:00+00:00")
    later = bar(minute="14:45", o=110, h=126, low=105, close=124)
    paused = run([], frames(later), now="2026-11-17T14:50:00+00:00", state=first)
    assert paused["trades"] == []
    assert paused["coverage_gaps"]["ABC"]["reason"] == "intraday_coverage_gap"
    assert paused["state"]["last_bar_at"]["ABC"] == first["state"]["last_bar_at"]["ABC"]
    assert paused["positions"][0]["stale"]
    recovered = run([], frames(bar(minute="14:40", h=102, low=96, close=98), later),
                    now="2026-11-17T14:50:00+00:00", state=paused)
    assert recovered["trades"][0]["exit_reason"] == "stop"
    assert recovered["trades"][0]["exit_timestamp"] == "2026-11-17T14:45:00+00:00"
    assert recovered["coverage_gaps"] == {}


def test_next_session_requires_observed_previous_close_and_actual_open():
    first = run([signal()], frames(bar(minute="20:55")), now="2026-11-17T21:05:00+00:00")
    opening = bar(day="2026-11-18", minute="14:30", o=90, h=93, low=89, close=92)
    valid = run([], {"SPX": [bar(), opening], "ABC": [opening]}, now="2026-11-18T14:35:00+00:00", state=first)
    assert valid["trades"][0]["exit_reason"] == "stop_gap"
    missing_open = bar(day="2026-11-18", minute="14:35", o=110, h=126, low=105, close=124)
    paused = run([], {"SPX": [bar(), opening, missing_open], "ABC": [missing_open]},
                 now="2026-11-18T14:40:00+00:00", state=first)
    assert not paused["trades"] and paused["coverage_gaps"]["ABC"]


def test_missing_entire_intermediate_session_does_not_fabricate_later_exit():
    first = run([signal()], frames(bar(minute="20:55")), now="2026-11-17T21:05:00+00:00")
    skipped = bar(day="2026-11-18", minute="14:30")
    later = bar(day="2026-11-19", minute="14:30", h=126)
    paused = run([], {"SPX": [bar(), skipped, later], "ABC": [later]},
                 now="2026-11-19T14:35:00+00:00", state=first)
    assert not paused["trades"] and paused["coverage_gaps"]["ABC"]


@pytest.mark.parametrize("key,value", [("stop_loss_pct", 0), ("stop_loss_pct", 1), ("take_profit_pct", -1), ("fee_bps", float('nan'))])
def test_invalid_execution_settings_fail_closed(key, value):
    with pytest.raises(ValueError):
        apply_intraday([], {}, now="2026-11-17T14:45:00+00:00", config={**CONFIG, key: value})


def yahoo_payload(symbol="ABC"):
    def stamp(text): return int(datetime.fromisoformat(text).timestamp())
    start = stamp("2026-11-17T14:30:00+00:00")
    return {"chart": {"error": None, "result": [{"meta": {"symbol": symbol, "currency": "USD", "exchangeTimezoneName": "America/New_York", "dataGranularity": "5m",
        "currentTradingPeriod": {"regular": {"start": start, "end": start + 23400}},
        "tradingPeriods": [[{"start": start, "end": start + 23400}]]},
        "timestamp": [start, start + 300, start + 600, start + 607],
        "indicators": {"quote": [{"open": [100, 101, None, 102], "high": [102, 103, None, 102], "low": [99, 100, None, 102], "close": [101, 102, None, 102], "volume": [1000, 1000, None, 0]}]}}]}}


class Response:
    def __init__(self, payload): self.payload = json.dumps(payload).encode()
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self): return self.payload


def test_loader_filters_partial_null_and_synthetic_quote_and_archives_compact_receipt(tmp_path):
    def request(req, **kwargs):
        symbol = "^GSPC" if '%5EGSPC' in req.full_url else "ABC"
        return Response(yahoo_payload(symbol))
    with patch("urllib.request.urlopen", side_effect=request):
        result = fetch_yahoo_intraday(["ABC"], now="2026-11-17T14:42:00+00:00", cache_dir=tmp_path)
    assert set(result["bars"]) == {"ABC", "SPX"}
    assert len(result["bars"]["ABC"]) == 2
    assert result["sources"]["ABC"]["latest_completed_at"] == "2026-11-17T14:40:00+00:00"
    assert not result["sources"]["ABC"]["stale"]
    receipt = json.loads(next((tmp_path / '2026-11-17').glob('ABC_*.json')).read_text())
    assert "latest_bar" in receipt and "bars" not in receipt


def test_normalized_bar_archive_is_write_once_and_content_addressed(tmp_path):
    requests = []
    def request(req, **kwargs):
        payload = yahoo_payload("^GSPC" if '%5EGSPC' in req.full_url else "ABC")
        requests.append(req.full_url)
        payload['chart']['result'][0]['meta']['regularMarketTime'] = len(requests)
        return Response(payload)
    with patch("urllib.request.urlopen", side_effect=request):
        fetch_yahoo_intraday(["ABC"], now="2026-11-17T14:42:00+00:00", cache_dir=tmp_path)
        originals = {p: p.read_bytes() for p in (tmp_path / 'bars').rglob('*.json')}
        fetch_yahoo_intraday(["ABC"], now="2026-11-17T14:43:00+00:00", cache_dir=tmp_path)
    assert len(originals) == 4
    assert {p: p.read_bytes() for p in (tmp_path / 'bars').rglob('*.json')} == originals
    assert all(json.loads(value)["bar"]["content_sha256"] in str(path)
               for path, value in originals.items())


def test_source_receipt_time_is_actual_read_time_separate_from_price_cutoff():
    class ReceiptClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 17, 14, 42, 9, tzinfo=timezone.utc)
    def request(req, **kwargs):
        return Response(yahoo_payload("^GSPC" if '%5EGSPC' in req.full_url else "ABC"))
    with patch("urllib.request.urlopen", side_effect=request), patch("multiedge.intraday.datetime", ReceiptClock):
        result = fetch_yahoo_intraday(["ABC"], now="2026-11-17T14:42:00+00:00")
    assert result["sources"]["ABC"]["check_as_of"] == "2026-11-17T14:42:00+00:00"
    assert result["sources"]["ABC"]["received_at"] == "2026-11-17T14:42:09+00:00"


def test_loader_does_not_emit_stale_quotes_or_claim_zero_exchange_delay():
    def request(req, **kwargs):
        return Response(yahoo_payload("^GSPC" if '%5EGSPC' in req.full_url else "ABC"))
    with patch("urllib.request.urlopen", side_effect=request):
        result = fetch_yahoo_intraday(["ABC"], now="2026-11-17T15:00:00+00:00")
    assert result["bars"]["ABC"] == [] and result["sources"]["ABC"]["stale"]
    assert result["sources"]["ABC"]["exchange_delay_seconds"] is None


def test_loader_surfaces_provider_failure_per_symbol_without_fake_quotes():
    with patch("urllib.request.urlopen", side_effect=URLError("offline")):
        result = fetch_yahoo_intraday(["ABC"], now="2026-11-17T14:42:00+00:00")
    assert set(result["errors"]) == {"ABC", "SPX"}
    assert not result["bars"]


def test_after_close_reconciliation_uses_actual_close_not_wallclock_age():
    def request(req, **kwargs):
        data = yahoo_payload("^GSPC" if '%5EGSPC' in req.full_url else "ABC")
        item = data['chart']['result'][0]
        item['timestamp'] = [int(datetime.fromisoformat('2026-11-17T20:55:00+00:00').timestamp())]
        item['indicators']['quote'][0] = {"open": [100], "high": [102], "low": [99], "close": [101], "volume": [1000]}
        return Response(data)
    with patch("urllib.request.urlopen", side_effect=request):
        result = fetch_yahoo_intraday(["ABC"], now="2026-11-17T23:30:00+00:00")
    assert not result["sources"]["ABC"]["stale"]
    assert result["sources"]["ABC"]["delay_seconds"] == 0
    assert result["sources"]["ABC"]["wallclock_age_seconds"] == 9000
    assert len(result["bars"]["ABC"]) == 1


def test_loader_preserves_actual_split_events_without_inventing_missing_events():
    def request(req, **kwargs):
        data = yahoo_payload("^GSPC" if '%5EGSPC' in req.full_url else "ABC")
        data['chart']['result'][0]['events'] = {'splits': {'s': {'date': 1794925800, 'numerator': 2, 'denominator': 1, 'splitRatio': '2:1'}}}
        return Response(data)
    with patch("urllib.request.urlopen", side_effect=request):
        result = fetch_yahoo_intraday(["ABC"], now="2026-11-17T14:42:00+00:00")
    assert result["sources"]["ABC"]["splits"][0]['date'] == '2026-11-17'
    assert result["sources"]["ABC"]["splits"][0]['numerator'] == 2
    assert result["sources"]["ABC"]["corporate_events_available"]


def test_receipt_failure_does_not_leave_executable_unarchived_quotes(tmp_path):
    def request(req, **kwargs):
        return Response(yahoo_payload("^GSPC" if '%5EGSPC' in req.full_url else "ABC"))
    with patch("urllib.request.urlopen", side_effect=request), patch("multiedge.intraday._archive", side_effect=OSError("disk full")):
        result = fetch_yahoo_intraday(["ABC"], now="2026-11-17T14:42:00+00:00", cache_dir=tmp_path)
    assert result["bars"] == {} and set(result["errors"]) == {"ABC", "SPX"}
