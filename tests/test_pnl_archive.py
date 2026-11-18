"""Historical per-stock marks must use unambiguous, accepted-session evidence."""
import copy
import json

from multiedge.pnl_archive import archived_daily_marks


def account():
    return {"execution_model": "intraday_5m", "last_checked_at": "2026-11-18T15:00:10+00:00",
            "data_as_of": "2026-11-18T15:00:00+00:00", "positions": [],
            "trades": [{"ticker": "AMGN", "source_signal_id": "a", "entry_date": "2026-11-17",
                        "entry_timestamp": "2026-11-17T17:50:00+00:00", "exit_date": "2026-11-18"}],
            "state": {"last_bar_at": {"AMGN": "2026-11-18T14:55:00+00:00"}}, "equity_curve": []}


def archive(root, ticker="AMGN", *, price=450, received="2026-11-17T21:00:10+00:00", suffix="a", day="2026-11-17", close="21:00"):
    directory = root / "bars" / day
    directory.mkdir(parents=True, exist_ok=True)
    record = {"ticker": ticker, "received_at": received, "raw_sha256": "raw-" + suffix,
              "bar": {"timestamp": day + "T" + close[:3] + "55:00+00:00" if close == "21:00" else day + "T17:55:00+00:00",
                      "end": day + "T" + close + ":00+00:00", "session_start": day + "T14:30:00+00:00",
                      "session_end": day + "T" + close + ":00+00:00", "close": price,
                      "content_sha256": suffix, "source_sha256": "raw-" + suffix}}
    # Last regular bar opens five minutes before the actual exchange close.
    if close == "21:00":
        record["bar"]["timestamp"] = day + "T20:55:00+00:00"
    (directory / f"{ticker}_{suffix}.json").write_text(json.dumps(record))


def test_archive_recovers_completed_historical_close_without_mutating_account(tmp_path):
    archive(tmp_path)
    archive(tmp_path, "SPX")
    value = account()
    original = copy.deepcopy(value)
    marks, sessions, notes = archived_daily_marks(value, tmp_path)
    assert sessions == ["2026-11-17"]
    assert len(marks) == 1 and marks[0]["price"] == 450
    assert marks[0]["source_signal_id"] == "a" and marks[0]["complete"] is True
    assert marks[0]["basis"] == "archive_reconstruction"
    assert value == original and not notes


def test_conflicting_close_versions_are_missing_instead_of_arbitrarily_selected(tmp_path):
    archive(tmp_path)
    archive(tmp_path, price=451, suffix="b")
    archive(tmp_path, "SPX")
    marks, _, notes = archived_daily_marks(account(), tmp_path)
    assert marks == []
    assert any("AMGN" in note and "冲突" in note for note in notes)


def test_recorded_monitor_receipt_selects_the_used_version_before_later_revision(tmp_path):
    archive(tmp_path)
    archive(tmp_path, price=451, suffix="b", received="2026-11-17T21:05:10+00:00")
    archive(tmp_path, "SPX")
    check_at = "2026-11-17T21:00:07+00:00"
    directory = tmp_path / "2026-11-17"
    directory.mkdir()
    bar = json.loads((tmp_path / "bars" / "2026-11-17" / "AMGN_a.json").read_text())["bar"]
    (directory / "AMGN_receipt.json").write_text(json.dumps({"ticker": "AMGN", "check_as_of": check_at,
        "received_at": "2026-11-17T21:00:10+00:00", "raw_sha256": "raw-a", "latest_bar": bar}))
    value = account()
    value["equity_curve"] = [{"date": "2026-11-17T21:00:00+00:00", "recorded_at": check_at,
                               "stale_positions": 0}]
    marks, _, notes = archived_daily_marks(value, tmp_path)
    assert len(marks) == 1 and marks[0]["price"] == 450
    assert marks[0]["account_check_at"] == check_at
    assert not notes


def test_future_receipt_cannot_supply_past_account_history(tmp_path):
    archive(tmp_path, received="2026-11-19T21:00:10+00:00")
    archive(tmp_path, "SPX")
    marks, _, _ = archived_daily_marks(account(), tmp_path)
    assert marks == []


def test_current_live_mark_and_unprocessed_days_are_never_replaced_by_archive(tmp_path):
    archive(tmp_path)
    archive(tmp_path, "SPX")
    value = account()
    position = value["trades"].pop()
    position.update(mark_date="2026-11-17", mark_timestamp="2026-11-17T18:00:00+00:00", mark=449)
    value["positions"].append(position)
    value["state"]["last_bar_at"]["AMGN"] = "2026-11-17T17:55:00+00:00"
    assert archived_daily_marks(value, tmp_path)[0] == []


def test_early_close_and_corrupt_files_are_handled_without_fixed_1600_assumption(tmp_path):
    archive(tmp_path, day="2026-11-27", close="18:00", received="2026-11-27T18:01:00+00:00")
    archive(tmp_path, "SPX", day="2026-11-27", close="18:00", received="2026-11-27T18:01:00+00:00")
    (tmp_path / "bars" / "2026-11-27" / "AMGN_bad.json").write_text("bad json")
    value = account()
    value.update(last_checked_at="2026-11-30T15:00:10+00:00", data_as_of="2026-11-30T15:00:00+00:00")
    value["trades"][0].update(entry_date="2026-11-27", entry_timestamp="2026-11-27T15:00:00+00:00", exit_date="2026-11-30")
    value["state"]["last_bar_at"]["AMGN"] = "2026-11-30T14:55:00+00:00"
    marks, sessions, notes = archived_daily_marks(value, tmp_path)
    assert marks[0]["timestamp"] == "2026-11-27T18:00:00+00:00"
    assert sessions == ["2026-11-27"] and any("损坏" in note for note in notes)
