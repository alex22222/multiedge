"""Read-only per-stock daily attribution from Paper fills and dated marks."""
from datetime import date, datetime, timezone
import math
from zoneinfo import ZoneInfo


NY = ZoneInfo("America/New_York")


def _stamp(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return stamp.astimezone(timezone.utc) if stamp.tzinfo is not None else None
    except (ValueError, TypeError):
        return None


def _day(value):
    stamp = _stamp(value)
    return stamp.astimezone(NY).date().isoformat() if stamp else date.fromisoformat(str(value)[:10]).isoformat()


def record_daily_mark(state, position, *, price, timestamp, session_end=None, recorded_at=None,
                      source_sha256=None, content_sha256=None, interval="5m"):
    """Save the latest accepted valuation per actual position and NY session."""
    day = _day(timestamp)
    value = {"ticker": position["ticker"], "source_signal_id": position["source_signal_id"],
             "date": day, "price": price, "timestamp": timestamp, "session_end": session_end,
             "recorded_at": recorded_at, "source_sha256": source_sha256,
             "content_sha256": content_sha256, "interval": interval, "basis": "paper_ledger",
             "complete": interval == "1d" or bool(session_end and _stamp(timestamp) == _stamp(session_end))}
    marks = state.setdefault("daily_marks", [])
    for index, previous in enumerate(marks):
        if (previous["source_signal_id"], previous["date"]) == (value["source_signal_id"], day):
            marks[index] = value
            break
    else:
        marks.append(value)


def _normalized_mark(value, basis):
    if not value.get("source_signal_id"):
        return None
    try:
        price = float(value["price"])
        if not math.isfinite(price) or price <= 0:
            return None
        day = _day(value["date"])
        timestamp = value.get("timestamp") or day
        if _day(timestamp) != day:
            return None
        stamp, end = _stamp(timestamp), _stamp(value.get("session_end"))
        complete = bool(value.get("complete") and ((stamp is not None and stamp == end)
                        or (basis == "paper_ledger" and value.get("interval") == "1d")))
        return {**value, "price": price, "date": day, "timestamp": timestamp,
                "basis": basis, "complete": complete}
    except (ValueError, TypeError, KeyError):
        return None


def _basis(values):
    unique = {value for value in values if value}
    return next(iter(unique)) if len(unique) == 1 else "mixed" if unique else "paper_ledger"


def _marks(account, records, archived_marks):
    state = account.get("state", account)
    groups = {}
    for basis, values in (("archive_reconstruction", archived_marks or []),
                           ("paper_ledger", state.get("daily_marks", account.get("daily_marks", [])))):
        for raw in values:
            value = _normalized_mark(raw, basis)
            if value is not None:
                groups.setdefault((value["source_signal_id"], value["date"]), []).append(value)
    # A live position proves its last accepted mark. A closed trade does not:
    # gap exits copy today's opening mark with the previous mark's timestamp.
    for record in records:
        if record.get("exit_date") or record.get("mark") is None or not record.get("mark_date"):
            continue
        raw = {"ticker": record["ticker"], "source_signal_id": record["source_signal_id"],
               "date": record["mark_date"], "price": record["mark"],
               "timestamp": record.get("mark_timestamp") or record["mark_date"],
               "session_end": record.get("mark_session_end"),
               "interval": "5m" if record.get("mark_timestamp") else "1d",
               "complete": not record.get("mark_timestamp") or record.get("mark_timestamp") == record.get("mark_session_end")}
        value = _normalized_mark(raw, "paper_ledger")
        if value is not None:
            groups.setdefault((value["source_signal_id"], value["date"]), []).append(value)
    marks, conflicts = {}, set()
    for key, values in groups.items():
        native = [value for value in values if value["basis"] == "paper_ledger"]
        values = native or values
        latest = max(value["timestamp"] for value in values)
        candidates = [value for value in values if value["timestamp"] == latest]
        if len({value["price"] for value in candidates}) > 1:
            conflicts.add(key)
        else:
            marks[key] = max(candidates, key=lambda value: value["complete"])
    return marks, conflicts


def _valuation(record, day, marks, conflicts, current_day=None):
    quantity, entry = record["quantity"], record["entry_price"]
    entry_fee = record.get("entry_fee", 0)
    exit_day = record.get("exit_date")
    if day < record["entry_date"]:
        return {"value": 0, "realized": 0, "unrealized": 0, "fees": 0,
                "quantity": 0, "mark": None, "mark_at": None, "basis": None,
                "quality": "complete", "reason": None}
    if exit_day and day >= exit_day:
        net = record["net_pnl"]
        return {"value": net, "realized": net, "unrealized": 0,
                "fees": entry_fee + record.get("exit_fee", 0), "quantity": 0,
                "mark": record["exit_price"], "mark_at": record.get("exit_timestamp") or exit_day,
                "basis": "trade_ledger", "quality": "complete", "reason": None}
    key = (record["source_signal_id"], day)
    mark = marks.get(key)
    reason = None
    if key in conflicts:
        reason = "同一时刻存在冲突的归档价格版本"
        mark = None
    if mark is None:
        candidates = [value for (identity, mark_day), value in marks.items()
                      if identity == record["source_signal_id"] and record["entry_date"] <= mark_day < day]
        mark = max(candidates, key=lambda value: value["timestamp"]) if candidates else None
        reason = reason or "缺少本交易日已核验估值"
    if day == current_day and (record.get("stale") or record.get("data_error")):
        reason = "当前持仓行情已过期或处理中断，保留最后有效估值"
    price = mark["price"] if mark else entry
    net = quantity * (price - entry) - entry_fee
    return {"value": net, "realized": 0, "unrealized": net, "fees": entry_fee,
            "quantity": quantity, "mark": price,
            "mark_at": mark["timestamp"] if mark else record.get("entry_timestamp") or record["entry_date"],
            "basis": mark["basis"] if mark else "trade_ledger",
            "quality": "missing" if reason else "complete" if mark["complete"] else "partial",
            "reason": reason}


def build_daily_pnl(account, *, archived_marks=None, sessions=None):
    """Attribute daily net contribution without altering fills or ledger history.

    Archive inputs must already be bound to source_signal_id and limited to
    eligible, accepted chronology by the caller. Missing previous closes make
    daily P&L unknown; the latest known cumulative valuation remains visible.
    """
    state = account.get("state", account)
    records = list(account.get("trades", state.get("trades", []))) + list(account.get("positions", state.get("positions", [])))
    marks, conflicts = _marks(account, records, archived_marks)
    dates = {_day(value.get("date") if isinstance(value, dict) else value) for value in sessions or []}
    dates.update(value["date"] for value in marks.values())
    dates.update(record["entry_date"] for record in records)
    dates.update(record["exit_date"] for record in records if record.get("exit_date"))
    dates.update(_day(point["date"]) for point in state.get("equity_curve", account.get("equity_curve", [])))
    cutoff = account.get("as_of") or state.get("as_of") or account.get("last_checked_at") or state.get("last_checked_at")
    as_of = _day(cutoff) if cutoff else max(dates, default=None)
    calendar = sorted(day for day in dates if date.fromisoformat(day).weekday() < 5 and (as_of is None or day <= as_of))
    by_ticker = {}
    for record in records:
        by_ticker.setdefault(record["ticker"], []).append(record)
    rows = []
    for ticker, lots in sorted(by_ticker.items()):
        first = min(record["entry_date"] for record in lots)
        last = max(record.get("exit_date") or as_of or first for record in lots)
        for index, day in enumerate(calendar):
            if day < first or day > last:
                continue
            current = [_valuation(record, day, marks, conflicts, current_day=as_of) for record in lots]
            previous_day = calendar[index - 1] if index else None
            previous = [_valuation(record, previous_day, marks, conflicts) if previous_day else
                        {"value": 0, "quality": "complete" if record["entry_date"] >= day else "missing", "basis": None}
                        for record in lots]
            reasons = [value["reason"] for value in current if value.get("reason")]
            if any(value["quality"] != "complete" for value in previous):
                reasons.append("缺少前一交易日完整收盘估值，无法计算单日变化")
            unknown = bool(reasons)
            quality = "missing" if unknown else "partial" if any(value["quality"] == "partial" for value in current) else "complete"
            dated = [value for value in current if value["mark_at"] is not None]
            last_mark = max(dated, key=lambda value: value["mark_at"]) if dated else {}
            rows.append({"date": day, "ticker": ticker,
                         "daily_pnl": None if unknown else sum(value["value"] for value in current) - sum(value["value"] for value in previous),
                         "cumulative_pnl": sum(value["value"] for value in current),
                         "realized_pnl": sum(value["realized"] for value in current),
                         "unrealized_pnl": sum(value["unrealized"] for value in current),
                         "fees": sum(record.get("entry_fee", 0) for record in lots if record["entry_date"] == day)
                                 + sum(record.get("exit_fee", 0) for record in lots if record.get("exit_date") == day),
                         "cumulative_fees": sum(value["fees"] for value in current),
                         "quantity": sum(value["quantity"] for value in current),
                         "mark": last_mark.get("mark"), "mark_at": last_mark.get("mark_at"),
                         "status": "holding" if any(value["quantity"] for value in current) else "closed",
                         "quality": quality, "basis": _basis(value["basis"] for value in current + previous),
                         "missing_reason": "；".join(dict.fromkeys(reasons)) or None})
    rows.sort(key=lambda row: (-date.fromisoformat(row["date"]).toordinal(), row["ticker"]))
    summaries = {}
    for row in rows:
        summaries.setdefault(row["ticker"], row.copy())
    return {"currency": "USD", "timezone": "America/New_York", "rows": rows,
            "summaries": list(summaries.values()), "as_of": as_of,
            "notes": ["每日净盈亏为截至本日累计净贡献减去前一交易日累计净贡献；买卖费用在发生日扣除，滑点已计入成交价。",
                      "已实现与未实现列为截至本日的累计值；盘中估值会继续变化，缺前收盘或本日行情时不填零。",
                      "归档重建仅补充历史估值，不重做或改变已登记的成交。未包含分红、拆股及税务会计。"]}
