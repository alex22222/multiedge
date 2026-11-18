"""Per-stock daily P&L uses actual fills and dated, attributable valuations."""
import copy

import pytest

from multiedge.pnl import build_daily_pnl


def lot(**values):
    return {"ticker": "ABC", "source_signal_id": "s1", "quantity": 10,
            "entry_price": 100, "entry_fee": 1, "entry_date": "2026-11-16",
            "entry_timestamp": "2026-11-16T14:30:00+00:00", **values}


def mark(day, price, *, complete=True, signal="s1", ticker="ABC", basis="paper_ledger"):
    return {"ticker": ticker, "source_signal_id": signal, "date": day, "price": price,
            "timestamp": f"{day}T{'21:00' if complete else '15:00'}:00+00:00",
            "session_end": f"{day}T21:00:00+00:00", "complete": complete,
            "recorded_at": f"{day}T21:01:00+00:00", "basis": basis,
            "source_sha256": f"source-{day}"}


def account(*, positions=(), trades=(), marks=(), as_of="2026-11-18", **values):
    return {"positions": list(positions), "trades": list(trades), "as_of": as_of,
            "state": {"daily_marks": list(marks)}, **values}


def rows_by_day(report):
    return {row["date"]: row for row in report["rows"]}


def test_multiday_exit_is_daily_change_not_whole_trade_profit():
    trade = lot(exit_date="2026-11-18", exit_timestamp="2026-11-18T15:00:00+00:00",
                exit_price=108, exit_fee=2, net_pnl=77)
    result = build_daily_pnl(account(trades=[trade], marks=[mark("2026-11-16", 105), mark("2026-11-17", 110)]),
                             sessions=["2026-11-16", "2026-11-17", "2026-11-18"])
    rows = rows_by_day(result)
    assert rows["2026-11-16"]["daily_pnl"] == 49
    assert rows["2026-11-17"]["daily_pnl"] == 50
    assert rows["2026-11-18"]["daily_pnl"] == -22
    assert rows["2026-11-18"]["cumulative_pnl"] == 77
    assert rows["2026-11-18"]["realized_pnl"] == 77
    assert rows["2026-11-18"]["unrealized_pnl"] == 0
    assert rows["2026-11-18"]["fees"] == 2
    assert rows["2026-11-18"]["cumulative_fees"] == 3
    assert sum(row["daily_pnl"] for row in result["rows"]) == trade["net_pnl"]
    assert result["rows"][0]["date"] == result["summaries"][0]["date"] == "2026-11-18"


def test_intraday_open_position_is_partial_and_includes_entry_fee_once():
    position = lot()
    result = build_daily_pnl(account(positions=[position], marks=[mark("2026-11-16", 105), mark("2026-11-17", 110, complete=False)], as_of="2026-11-17"))
    row = result["summaries"][0]
    assert row["daily_pnl"] == 50
    assert row["cumulative_pnl"] == row["unrealized_pnl"] == 99
    assert row["fees"] == row["realized_pnl"] == 0
    assert row["quality"] == "partial"
    assert row["status"] == "holding" and row["quantity"] == 10


def test_missing_previous_close_does_not_turn_total_profit_into_daily_profit():
    position = lot(mark=110, mark_date="2026-11-18", mark_timestamp="2026-11-18T15:00:00+00:00")
    result = build_daily_pnl(account(positions=[position], marks=[mark("2026-11-16", 105)]),
                             sessions=["2026-11-16", "2026-11-17", "2026-11-18"])
    rows = rows_by_day(result)
    assert rows["2026-11-17"]["daily_pnl"] is None
    assert rows["2026-11-18"]["daily_pnl"] is None
    assert rows["2026-11-18"]["cumulative_pnl"] == 99
    assert rows["2026-11-18"]["quality"] == "missing"
    assert rows["2026-11-18"]["missing_reason"]


def test_previous_partial_mark_is_not_treated_as_previous_close():
    result = build_daily_pnl(account(positions=[lot()], marks=[mark("2026-11-16", 105, complete=False), mark("2026-11-17", 110)], as_of="2026-11-17"))
    assert result["summaries"][0]["daily_pnl"] is None
    assert result["summaries"][0]["cumulative_pnl"] == 99


def test_same_day_roundtrip_needs_no_quote_mark():
    trade = lot(exit_date="2026-11-16", exit_timestamp="2026-11-16T15:00:00+00:00",
                exit_price=99, exit_fee=2, net_pnl=-13)
    result = build_daily_pnl(account(trades=[trade]), sessions=["2026-11-16", "2026-11-17", "2026-11-18"])
    assert len(result["rows"]) == 1
    row = result["rows"][0]
    assert row["daily_pnl"] == row["cumulative_pnl"] == -13
    assert row["fees"] == 3 and row["quantity"] == 0
    assert row["quality"] == "complete" and row["basis"] == "trade_ledger"


def test_archived_marks_are_explicitly_reconstructed_without_mutating_account():
    original = account(positions=[lot()], as_of="2026-11-17")
    saved = copy.deepcopy(original)
    archive = [mark("2026-11-16", 105, basis="archive_reconstruction"), mark("2026-11-17", 110, basis="archive_reconstruction")]
    result = build_daily_pnl(original, archived_marks=archive)
    assert original == saved
    assert result["summaries"][0]["daily_pnl"] == 50
    assert result["summaries"][0]["basis"] == "archive_reconstruction"


def test_accepted_ledger_mark_wins_over_conflicting_archive():
    result = build_daily_pnl(account(positions=[lot()], marks=[mark("2026-11-16", 105)], as_of="2026-11-16"),
                             archived_marks=[mark("2026-11-16", 999, basis="archive_reconstruction")])
    assert result["summaries"][0]["cumulative_pnl"] == 49
    assert result["summaries"][0]["basis"] == "paper_ledger"


def test_conflicting_archived_revisions_are_missing_instead_of_arbitrarily_selected():
    a = mark("2026-11-16", 105, basis="archive_reconstruction")
    b = {**a, "price": 106, "source_sha256": "other"}
    result = build_daily_pnl(account(positions=[lot()], as_of="2026-11-16"), archived_marks=[a, b])
    assert result["summaries"][0]["daily_pnl"] is None
    assert result["summaries"][0]["quality"] == "missing"


def test_unbound_archive_does_not_leak_into_another_trade():
    orphan = {k: v for k, v in mark("2026-11-16", 105).items() if k != "source_signal_id"}
    result = build_daily_pnl(account(positions=[lot()], as_of="2026-11-16"), archived_marks=[orphan])
    assert result["summaries"][0]["daily_pnl"] is None


def test_reentry_aggregates_old_realized_and_new_unrealized_without_duplicate_rows():
    first = lot(exit_date="2026-11-16", exit_price=102, exit_fee=1, net_pnl=18)
    second = lot(source_signal_id="s2", entry_date="2026-11-17", entry_price=105, entry_fee=2,
                 entry_timestamp="2026-11-17T14:30:00+00:00", quantity=5)
    result = build_daily_pnl(account(positions=[second], trades=[first], marks=[mark("2026-11-17", 110, signal="s2")], as_of="2026-11-17"))
    row = result["summaries"][0]
    assert len(result["rows"]) == 2
    assert result["rows"][1]["daily_pnl"] == 18
    assert row["daily_pnl"] == 23
    assert row["realized_pnl"] == 18 and row["unrealized_pnl"] == 23
    assert row["cumulative_pnl"] == 41 and row["quantity"] == 5
    assert sum(value["daily_pnl"] for value in result["rows"]) == 41


def test_same_day_stale_mark_keeps_cumulative_value_but_not_current_daily_pnl():
    position = lot(mark=110, mark_date="2026-11-17", mark_timestamp="2026-11-17T15:00:00+00:00",
                   stale=True, data_error="intraday_coverage_gap")
    result = build_daily_pnl(account(positions=[position], marks=[mark("2026-11-16", 105), mark("2026-11-17", 110, complete=False)], as_of="2026-11-17"))
    row = result["summaries"][0]
    assert row["daily_pnl"] is None and row["quality"] == "missing"
    assert row["cumulative_pnl"] == 99 and row["mark_at"] == "2026-11-17T15:00:00+00:00"
    assert rows_by_day(result)["2026-11-16"]["daily_pnl"] == 49


def test_closed_trade_inherited_mark_cannot_rewrite_previous_close():
    # The gap-exit path sets mark=open before copying a position into trades;
    # its inherited previous mark_timestamp does not identify that price.
    trade = lot(exit_date="2026-11-17", exit_timestamp="2026-11-17T14:30:00+00:00",
                exit_price=90, exit_fee=2, net_pnl=-103, mark=90,
                mark_date="2026-11-16", mark_timestamp="2026-11-16T21:00:00+00:00",
                mark_session_end="2026-11-16T21:00:00+00:00")
    result = build_daily_pnl(account(trades=[trade], as_of="2026-11-17"),
                             archived_marks=[mark("2026-11-16", 105, basis="archive_reconstruction")])
    rows = rows_by_day(result)
    assert rows["2026-11-16"]["cumulative_pnl"] == 49
    assert rows["2026-11-17"]["daily_pnl"] == -152


def test_pending_orders_and_non_trading_weekend_do_not_create_pnl_rows():
    pending = account(orders=[{"ticker": "XYZ", "status": "pending"}])
    assert build_daily_pnl(pending)["rows"] == []
    position = lot(entry_date="2026-11-13", entry_timestamp="2026-11-13T14:30:00+00:00")
    report = build_daily_pnl(account(positions=[position], marks=[mark("2026-11-13", 105), mark("2026-11-16", 110)], as_of="2026-11-16"),
                             sessions=["2026-11-13", "2026-11-14", "2026-11-15", "2026-11-16"])
    assert [r["date"] for r in report["rows"]] == ["2026-11-16", "2026-11-13"]
    assert report["summaries"][0]["daily_pnl"] == 50


def test_current_stale_mark_keeps_its_actual_time_and_missing_daily_value():
    position = lot(mark=105, mark_date="2026-11-16", mark_timestamp="2026-11-16T21:00:00+00:00",
                   mark_session_end="2026-11-16T21:00:00+00:00", stale=True)
    report = build_daily_pnl(account(positions=[position], as_of="2026-11-17"), sessions=["2026-11-16", "2026-11-17"])
    row = report["summaries"][0]
    assert row["daily_pnl"] is None and row["cumulative_pnl"] == 49
    assert row["mark_at"] == "2026-11-16T21:00:00+00:00"
    assert row["quality"] == "missing"


def test_complete_flag_requires_actual_session_end_timestamp():
    false_close = {**mark("2026-11-16", 105, complete=False), "complete": True}
    report = build_daily_pnl(account(positions=[lot()], marks=[false_close, mark("2026-11-17", 110)], as_of="2026-11-17"))
    assert report["summaries"][0]["daily_pnl"] is None


def test_new_york_dates_survive_daylight_saving_and_utc_calendar_edges():
    position = lot(entry_date="2026-07-01", entry_timestamp="2026-07-01T13:30:00+00:00")
    value = {**mark("2026-07-01", 105), "timestamp": "2026-07-01T20:00:00+00:00", "session_end": "2026-07-01T20:00:00+00:00"}
    report = build_daily_pnl(account(positions=[position], marks=[value], as_of="2026-07-01"))
    assert report["timezone"] == "America/New_York"
    assert report["rows"][0]["date"] == "2026-07-01"
    assert report["rows"][0]["quality"] == "complete"


def test_engine_records_only_accepted_intraday_marks_and_resume_is_idempotent():
    from multiedge.intraday import apply_intraday
    from tests.test_intraday import CONFIG, bar, frames, signal
    first = apply_intraday([signal()], frames(bar()), now="2026-11-17T14:40:00+00:00", config=CONFIG,
                           started_at="2026-11-17T14:32:00+00:00")
    marks = first["state"]["daily_marks"]
    assert len(marks) == 1
    assert marks[0]["price"] == 101 and not marks[0]["complete"]
    paused = apply_intraday([], frames(bar(minute="14:45", close=110, h=111)),
                            now="2026-11-17T14:50:00+00:00", config=CONFIG, state=first)
    assert paused["state"]["daily_marks"] == marks
    repeated = apply_intraday([], frames(bar()), now="2026-11-17T14:40:00+00:00", config=CONFIG, state=first)
    assert repeated["state"] == first["state"]


def test_engine_daily_marks_reconcile_and_survive_resuming():
    from multiedge.paper import run_paper
    from tests.test_paper import normal_frames, signal
    first = run_paper([signal()], normal_frames(), as_of="2025-01-07", initial_cash=10000,
                      risk_fraction=.01, fee_bps=0, slippage_bps=0)
    assert first["state"]["daily_marks"][0]["complete"]
    resumed = run_paper([], normal_frames(), as_of="2025-01-08", initial_cash=10000,
                        risk_fraction=.01, fee_bps=0, slippage_bps=0, state=first)
    report = build_daily_pnl(resumed)
    assert sum(r["daily_pnl"] for r in report["rows"]) == pytest.approx(resumed["equity"] - 10000)
    assert len(resumed["state"]["daily_marks"]) == 2


def test_intraday_session_close_replaces_partial_mark_and_retains_source_identity():
    from multiedge.intraday import apply_intraday
    from tests.test_intraday import CONFIG, bar, frames, signal
    opening = {**bar(minute="20:50"), "source_sha256": "raw-open"}
    closing = {**bar(minute="20:55", close=102), "source_sha256": "raw-close"}
    result = apply_intraday([signal(observed_at="2026-11-17T20:47:00+00:00")], frames(opening, closing),
                            now="2026-11-17T21:00:00+00:00", config=CONFIG,
                            started_at="2026-11-17T20:47:00+00:00")
    marks = result["state"]["daily_marks"]
    assert len(marks) == 1 and marks[0]["complete"]
    assert marks[0]["price"] == 102
    assert marks[0]["timestamp"] == marks[0]["session_end"] == "2026-11-17T21:00:00+00:00"
    assert marks[0]["source_sha256"] == "raw-close" and marks[0]["content_sha256"]
    report = build_daily_pnl(result)
    assert report["summaries"][0]["quality"] == "complete"
    assert report["summaries"][0]["daily_pnl"] == pytest.approx(result["equity"] - CONFIG["initial_cash"])
