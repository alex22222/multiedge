"""Read-only reconstruction of missing historical marks from retained quotes.

This does not replay orders, change fills or advance the account watermark.
Current marks always belong to the ledger, even when newer quotes are cached.
"""
from datetime import date, datetime, timedelta
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

from .research import validate_ticker


NY = ZoneInfo("America/New_York")


def _stamp(value):
    if not isinstance(value, str):
        raise ValueError("quote timestamp must be a string")
    value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("quote timestamp must include timezone")
    return value


def archived_daily_marks(account, root):
    """Return bound close marks, verified NY sessions and reconstruction issues."""
    ledger = account.get("state", account)
    positions = account.get("positions", ledger.get("positions", []))
    trades = account.get("trades", ledger.get("trades", []))
    lots = [*positions, *trades]
    if not lots or account.get("execution_model", ledger.get("execution_model")) != "intraday_5m":
        return [], [], []
    cutoff_value = account.get("last_checked_at") or ledger.get("last_checked_at")
    if not cutoff_value:
        return [], [], ["历史行情缺少账户核验时间，未重建每日估值"]
    cutoff = _stamp(cutoff_value)
    first_day = min(lot["entry_date"] for lot in lots)
    last_day = max(lot.get("exit_date") or lot.get("mark_date") or lot["entry_date"] for lot in lots)
    marks, sessions, notes = [], set(), set()
    native = {(mark["source_signal_id"], mark["date"]) for mark in ledger.get("daily_marks", [])}
    checks = {point["date"]: point["recorded_at"] for point in account.get("equity_curve", ledger.get("equity_curve", []))
              if point.get("recorded_at") and not point.get("stale_positions")}

    def used_version(day, ticker, closing):
        # Legacy monitors recorded the check's start time in the curve. Match
        # that exact receipt, rather than adopting a provider's later revision.
        check = checks.get(closing)
        identities = set()
        if check is None:
            return identities, None
        for path in (Path(root) / day).glob(f"{ticker}_*.json"):
            try:
                receipt = json.loads(path.read_text())
                bar = receipt.get("latest_bar") or {}
                if (receipt["ticker"] == ticker and receipt["check_as_of"] == check
                        and bar.get("end") == closing and _stamp(receipt["received_at"]) <= cutoff):
                    identities.add(bar["content_sha256"])
            except (ValueError, TypeError, KeyError, OSError):
                notes.add(f"{day} {ticker} 存在损坏的接收记录，已跳过")
        return identities, check

    def records(directory, ticker):
        for path in directory.glob(f"{ticker}_*.json"):
            try:
                value = json.loads(path.read_text())
                bar = value["bar"]
                received, start, end = (_stamp(value["received_at"]), _stamp(bar["timestamp"]), _stamp(bar["end"]))
                session_start, session_end = _stamp(bar["session_start"]), _stamp(bar["session_end"])
                if (value["ticker"] != ticker or end - start != timedelta(minutes=5)
                        or not session_start <= start < end <= session_end
                        or start.astimezone(NY).date().isoformat() != directory.name
                        or isinstance(bar["close"], bool) or not math.isfinite(bar["close"]) or bar["close"] <= 0
                        or not isinstance(bar["content_sha256"], str) or not isinstance(value["raw_sha256"], str)):
                    raise ValueError("invalid archived regular-session bar")
                if end <= received <= cutoff:
                    yield value
            except (ValueError, TypeError, KeyError, OSError):
                notes.add(f"{directory.name} {ticker} 存在损坏的归档，已跳过并保留缺失状态")

    for directory in sorted((Path(root) / "bars").glob("????-??-??")):
        if not directory.is_dir() or not first_day <= directory.name <= last_day:
            continue
        try:
            date.fromisoformat(directory.name)
        except ValueError:
            continue
        if not any(records(directory, "SPX")):
            continue
        day = directory.name
        sessions.add(day)
        for lot in lots:
            ticker, identity = lot["ticker"], lot["source_signal_id"]
            validate_ticker(ticker)
            end_day = lot.get("exit_date") or lot.get("mark_date") or lot["entry_date"]
            # Never substitute cached prices for a live/stale current ledger mark.
            if not lot["entry_date"] <= day < end_day or (identity, day) in native:
                continue
            watermark = ledger.get("last_bar_at", {}).get(ticker)
            if not watermark:
                continue
            accepted_end = _stamp(watermark) + timedelta(minutes=5)
            candidates = [record for record in records(directory, ticker)
                          if record["bar"]["end"] == record["bar"]["session_end"]
                          and _stamp(record["bar"]["end"]) <= accepted_end
                          and (not lot.get("entry_timestamp") or _stamp(lot["entry_timestamp"]) < _stamp(record["bar"]["end"]))]
            check = None
            if candidates:
                used, check = used_version(day, ticker, candidates[0]["bar"]["end"])
                if used:
                    candidates = [record for record in candidates if record["bar"]["content_sha256"] in used]
            fingerprints = {record["bar"]["content_sha256"] for record in candidates}
            if len(fingerprints) > 1:
                notes.add(f"{day} {ticker} 收盘归档版本冲突，未选择任一价格")
                continue
            if candidates:
                record = min(candidates, key=lambda value: _stamp(value["received_at"]))
                bar = record["bar"]
                marks.append({"ticker": ticker, "source_signal_id": identity, "date": day,
                              "price": bar["close"], "timestamp": bar["end"], "session_end": bar["session_end"],
                              "recorded_at": record["received_at"], "basis": "archive_reconstruction", "complete": True,
                              "source_sha256": record["raw_sha256"], "content_sha256": bar["content_sha256"],
                              "account_check_at": check})
    return marks, sorted(sessions), sorted(notes)
