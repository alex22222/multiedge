"""Deterministic long-only daily-bar Paper ledger, with no broker integration.

Signals are known after a completed close and may fill only at the next session
open. Supply SPX in frames as the reference calendar; otherwise the union of
supplied bars is used and cannot independently detect a missing market session.
Replay is retrospective simulation. Forward additionally requires a genuine
availability timestamp before the entry open. The caller owns provenance and
must not invent/backdate that timestamp.

Stops/targets are frozen at entry. Daily-bar dual touches use stop-first; gaps
through barriers fill at the open. No end-of-sample liquidation. Unadjusted bars
do not account for dividends/splits; data preparation must handle those events.
"""
import copy
from datetime import date, datetime, time
import hashlib
import json
import math
from numbers import Real
from zoneinfo import ZoneInfo

import pandas as pd

from .pnl import record_daily_mark


def _date(value):
    return date.fromisoformat(str(value)).isoformat()


def _number(value):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
        return None
    return float(value)


def _frames(frames):
    result = {}
    for ticker, frame in frames.items():
        if not {"Open", "High", "Low", "Close"} <= set(frame.columns):
            raise ValueError(f"{ticker}: OHLC columns required")
        frame = frame.copy()
        frame.index = pd.to_datetime(frame.index).tz_localize(None).normalize()
        if frame.index.has_duplicates:
            raise ValueError(f"{ticker}: duplicate session dates")
        result[str(ticker).upper()] = frame.sort_index()
    return result


def _bar(frames, ticker, day):
    frame = frames.get(ticker)
    stamp = pd.Timestamp(day)
    if frame is None or stamp not in frame.index:
        return None
    row = frame.loc[stamp]
    values = {key: _number(row[key]) for key in ("Open", "High", "Low", "Close")}
    if (any(value is None or value <= 0 for value in values.values())
            or values["High"] < max(values["Open"], values["Close"])
            or values["Low"] > min(values["Open"], values["Close"])):
        raise ValueError(f"{ticker}: invalid OHLC at {day}")
    return values


def _signal(value, exit_mode="structural"):
    ticker = str(value["ticker"]).strip().upper()
    if not ticker:
        raise ValueError("ticker is required")
    day = _date(value["signal_date"])
    observed = value.get("observed_at")
    if isinstance(observed, datetime):
        observed = observed.isoformat()
    if observed is not None and not isinstance(observed, str):
        raise ValueError("observed_at must be an ISO timestamp")
    target = _number(value.get("target"))
    if exit_mode == "structural" and value.get("target") is not None and (target is None or target <= 0):
        raise ValueError("target must be a positive finite number or null")
    return {"ticker": ticker, "signal_date": day, "stop": _number(value.get("stop")),
            "target": target,
            "signal_id": str(value.get("signal_id") or f"{ticker}:{day}"), "observed_at": observed}


def _fingerprint(signal):
    return hashlib.sha256(json.dumps(signal, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _event(state, kind, day, **values):
    state["events"].append({"sequence": len(state["events"]) + 1, "kind": kind, "date": day, **values})


def _skip(state, order, reason, day):
    order["status"], order["reason"] = "skipped", reason
    _event(state, "skipped", day, signal_id=order["signal_id"], ticker=order["ticker"], reason=reason)


def _close(state, position, raw_price, day, reason, fees, slip):
    price = raw_price * (1 - slip)
    proceeds = position["quantity"] * price
    exit_fee = proceeds * fees
    state["cash"] += proceeds - exit_fee
    state["fees_paid"] += exit_fee
    trade = {**position, "exit_date": day, "exit_price": price, "exit_fee": exit_fee,
             "exit_reason": reason,
             "net_pnl": proceeds - position["quantity"] * position["entry_price"]
                        - position["entry_fee"] - exit_fee}
    state["trades"].append(trade)
    state["positions"].remove(position)
    _event(state, "exit", day, signal_id=position["source_signal_id"], ticker=position["ticker"],
           quantity=position["quantity"], price=price, fee=exit_fee, reason=reason)


def _equity(state):
    return state["cash"] + sum(p["quantity"] * p["mark"] for p in state["positions"])


def _timestamp(value):
    if isinstance(value, datetime):
        stamp = value
    elif isinstance(value, str):
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return stamp if stamp.tzinfo is not None and stamp.utcoffset() is not None else None


def _forward_reason(order, entry_day, started_at=None):
    observed = order["observed_at"]
    if observed is None:
        return "missing_observed_at"
    stamp = _timestamp(observed)
    if stamp is None:
        return "invalid_observed_at"
    opening = datetime.combine(date.fromisoformat(entry_day), time(9, 30), ZoneInfo("America/New_York"))
    if stamp > opening:
        return "signal_observed_after_entry"
    if started_at is not None and started_at > opening:
        return "account_started_after_entry"
    # Session completion is verified upstream (exchange half-days need their
    # actual close); a fixed 16:00 cutoff here would reject valid early closes.
    if stamp.astimezone(ZoneInfo("America/New_York")).date().isoformat() < order["signal_date"]:
        return "signal_observed_before_session"
    return None


def run_paper(signals: list[dict], frames: dict[str, pd.DataFrame], *, as_of,
              mode="replay", initial_cash=100000, risk_fraction=.0035,
              fee_bps=5, slippage_bps=5, state=None, started_at=None,
              exit_mode="structural", stop_loss_pct=None, take_profit_pct=None) -> dict:
    """Return a JSON-persistable ledger and presentation-friendly summaries.

Resume with result['state'] (or the entire previous result). Existing accounting
settings and signal identities are immutable. Newly imported old signals and
missed entry sessions are skipped rather than rewriting processed history.
Orders at the same open are allocated deterministically by date/ticker/id.
    New forward accounts start at the real started_at timestamp; without it,
    their start is inferred from valid observations or falls back to as_of.
    Historical bars supply a calendar, never fictitious pre-registration marks.
"""
    as_of = _date(as_of)
    if mode not in ("replay", "forward"):
        raise ValueError("mode must be replay or forward")
    if exit_mode not in ("structural", "fixed_percent"):
        raise ValueError("exit_mode must be structural or fixed_percent")
    if exit_mode == "fixed_percent":
        stop_loss_pct, take_profit_pct = _number(stop_loss_pct), _number(take_profit_pct)
        if (stop_loss_pct is None or not 0 < stop_loss_pct < 1
                or take_profit_pct is None or not 0 < take_profit_pct <= 10):
            raise ValueError("fixed_percent requires 0 < stop_loss_pct < 1 and 0 < take_profit_pct ≤ 10")
    elif stop_loss_pct is not None or take_profit_pct is not None:
        raise ValueError("percent exit parameters require fixed_percent mode")
    registration = _timestamp(started_at)
    if mode == "forward" and started_at is not None and registration is None:
        raise ValueError("started_at must be a timezone-aware ISO timestamp")
    amounts = {"initial_cash": initial_cash, "risk_fraction": risk_fraction,
               "fee_bps": fee_bps, "slippage_bps": slippage_bps}
    if any(_number(value) is None for value in amounts.values()):
        raise ValueError("account configuration must contain finite numbers")
    if initial_cash <= 0 or not 0 < risk_fraction <= 1 or not 0 <= fee_bps < 10000 or not 0 <= slippage_bps < 10000:
        raise ValueError("invalid account configuration")
    config = {key: float(value) for key, value in amounts.items()}
    if exit_mode == "fixed_percent":
        config.update({"exit_mode": exit_mode, "stop_loss_pct": stop_loss_pct, "take_profit_pct": take_profit_pct})
    frames = _frames(frames)
    calendar_source = "SPX" if "SPX" in frames else "union_of_supplied_bars"
    reference = [frames["SPX"]] if "SPX" in frames else list(frames.values())
    calendar = sorted({str(stamp.date()) for frame in reference for stamp in frame.index if str(stamp.date()) <= as_of})
    resuming = state is not None
    if resuming:
        state = copy.deepcopy(state.get("state", state))
        if state.get("schema_version") != 1 or state.get("config") != config or state.get("mode") != mode:
            raise ValueError("Paper configuration cannot change when resuming an account")
        if state["as_of"] > as_of:
            raise ValueError("as_of cannot move backwards")
    else:
        state = {"schema_version": 1, "mode": mode, "config": config, "as_of": None,
                 "cash": float(initial_cash), "fees_paid": 0.0, "positions": [], "orders": [],
                 "trades": [], "events": [], "equity_curve": [], "processed_signals": {}}
    previous = state["as_of"]
    pending = sorted((_signal(value, exit_mode) for value in signals), key=lambda s: (s["signal_date"], s["ticker"], s["signal_id"]))
    if mode == "forward":
        known_start = bool(state.get("start_date") or state.get("started_at"))
        if "start_date" not in state:
            if registration is None:
                observations = [_timestamp(order.get("observed_at")) for order in state["orders"] + pending
                                if order["signal_date"] <= as_of]
                registration = min((stamp for stamp in observations if stamp is not None), default=None)
            state["started_at"] = registration.isoformat() if registration else None
            state["start_date"] = (registration.astimezone(ZoneInfo("America/New_York")).date().isoformat()
                                   if registration else previous or as_of)
        registration = _timestamp(state.get("started_at"))
        # A legacy account without registration metadata is not rewritten just
        # because its start can now be guessed; retain and label that history.
        if (known_start and not state.get("history_start_unverified")) or not resuming or started_at is not None:
            state["equity_curve"] = [point for point in state["equity_curve"] if point["date"] >= state["start_date"]]
        elif state["equity_curve"]:
            state["history_start_unverified"] = True
    for value in pending:
        if value["signal_date"] > as_of:
            continue
        identity = value["signal_id"]
        fingerprint = _fingerprint(value)
        if identity in state["processed_signals"]:
            if state["processed_signals"][identity] != fingerprint:
                raise ValueError("signal_id already exists with different content")
            continue
        state["processed_signals"][identity] = fingerprint
        order = {**value, "status": "pending", "reason": None, "entry_session": None}
        if exit_mode == "fixed_percent":
            order.update({"source_stop": value["stop"], "source_target": value["target"],
                          "stop": None, "target": None, "exit_mode": exit_mode,
                          "planned_stop_pct": stop_loss_pct, "planned_target_pct": take_profit_pct,
                          "planned_rr": take_profit_pct / stop_loss_pct})
        state["orders"].append(order)
        # Invalid stops cannot become executable just because fresh prices arrive.
        if exit_mode == "structural" and (value["stop"] is None or value["stop"] <= 0):
            event_day = max(value["signal_date"], state["start_date"]) if mode == "forward" else value["signal_date"]
            _skip(state, order, "invalid_stop", event_day)
    for order in state["orders"]:
        if order["status"] != "pending":
            continue
        entry = next((day for day in calendar if day > order["signal_date"]), None)
        if entry is None:
            continue
        order["entry_session"] = entry
        event_day = max(entry, state["start_date"]) if mode == "forward" else entry
        if order["signal_date"] not in calendar:
            _skip(state, order, "missing_signal_session", event_day)
            continue
        if mode == "forward":
            reason = _forward_reason(order, entry, registration)
            if reason is None and entry < state["start_date"]:
                reason = "account_started_after_entry"
            if reason:
                _skip(state, order, reason, event_day)
                continue
        if previous is not None and entry <= previous:
            _skip(state, order, "late_import", event_day)
    fees, slip = fee_bps / 10000, slippage_bps / 10000
    for day in calendar:
        if mode == "forward" and day < state["start_date"]:
            continue
        if previous is not None and day <= previous:
            continue
        # Gap exits happen at the open, before new orders compete for cash.
        for position in list(state["positions"]):
            bar = _bar(frames, position["ticker"], day)
            if bar is None:
                continue
            position["mark"] = bar["Open"]
            if bar["Open"] <= position["stop"]:
                _close(state, position, bar["Open"], day, "stop_gap", fees, slip)
            elif position["target"] is not None and bar["Open"] >= position["target"]:
                _close(state, position, bar["Open"], day, "target_gap", fees, slip)
        for order in sorted(state["orders"], key=lambda s: (s["signal_date"], s["ticker"], s["signal_id"])):
            if order["status"] != "pending" or order["entry_session"] != day:
                continue
            bar = _bar(frames, order["ticker"], day)
            if bar is None:
                _skip(state, order, "missing_entry_bar", day)
                continue
            if any(p["ticker"] == order["ticker"] for p in state["positions"]):
                _skip(state, order, "already_holding", day)
                continue
            price = bar["Open"] * (1 + slip)
            stop = price * (1 - stop_loss_pct) if exit_mode == "fixed_percent" else order["stop"]
            target = price * (1 + take_profit_pct) if exit_mode == "fixed_percent" else order["target"]
            if stop >= bar["Open"]:
                _skip(state, order, "stop_at_or_above_entry", day)
                continue
            if target is not None and target <= price:
                _skip(state, order, "target_at_or_below_entry", day)
                continue
            risk_size = math.floor(_equity(state) * risk_fraction / (price - stop))
            cash_size = math.floor(state["cash"] / (price * (1 + fees)))
            quantity = min(risk_size, cash_size)
            if quantity <= 0:
                _skip(state, order, "insufficient_budget", day)
                continue
            entry_fee = quantity * price * fees
            state["cash"] -= quantity * price + entry_fee
            state["fees_paid"] += entry_fee
            position = {"ticker": order["ticker"], "quantity": quantity, "entry_price": price,
                        "entry_date": day, "entry_fee": entry_fee, "stop": stop,
                        "target": target, "mark": price, "mark_date": day,
                        "exit_mode": exit_mode,
                        "planned_stop_pct": stop_loss_pct if exit_mode == "fixed_percent" else (price - stop) / price,
                        "planned_target_pct": take_profit_pct if exit_mode == "fixed_percent" else (target / price - 1 if target is not None else None),
                        "planned_rr": (target - price) / (price - stop) if target is not None else None,
                        "stale": False, "source_signal_id": order["signal_id"]}
            state["positions"].append(position)
            order["status"] = "filled"
            if exit_mode == "fixed_percent":
                order["stop"], order["target"] = stop, target
            _event(state, "entry", day, signal_id=order["signal_id"], ticker=order["ticker"],
                   quantity=quantity, price=price, fee=entry_fee, stop=stop, target=target,
                   exit_mode=exit_mode, planned_rr=position["planned_rr"])
        # Intraday proceeds cannot finance orders filled earlier at this same open.
        for position in list(state["positions"]):
            bar = _bar(frames, position["ticker"], day)
            if bar is None:
                continue
            stop_hit = bar["Low"] <= position["stop"]
            target_hit = position["target"] is not None and bar["High"] >= position["target"]
            if stop_hit:
                _close(state, position, position["stop"], day,
                       "stop_and_target" if target_hit else "stop", fees, slip)
            elif target_hit:
                _close(state, position, position["target"], day, "target", fees, slip)
            else:
                position["mark"], position["mark_date"] = bar["Close"], day
                record_daily_mark(state, position, price=bar["Close"], timestamp=day,
                                  interval="1d")
        for position in state["positions"]:
            position["stale"] = position["mark_date"] < day
        state["equity_curve"].append({"date": day, "cash": state["cash"], "equity": _equity(state),
                                      "stale_positions": sum(p["stale"] for p in state["positions"])})
    state["as_of"] = as_of
    equity = _equity(state)
    realized = sum(t["net_pnl"] for t in state["trades"])
    unrealized = sum(p["quantity"] * (p["mark"] - p["entry_price"]) - p["entry_fee"] for p in state["positions"])
    peak, drawdown = float(initial_cash), 0.0
    for point in state["equity_curve"]:
        peak = max(peak, point["equity"])
        drawdown = min(drawdown, point["equity"] / peak - 1)
    metrics = {"realized_pnl": realized, "unrealized_pnl": unrealized, "fees_paid": state["fees_paid"],
               "total_return": equity / initial_cash - 1, "max_drawdown": drawdown,
               "closed_trades": len(state["trades"]), "open_positions": len(state["positions"]),
               "stale_positions": sum(p["stale"] for p in state["positions"]),
               "win_rate": (sum(t["net_pnl"] > 0 for t in state["trades"]) / len(state["trades"])) if state["trades"] else None}
    data_as_of = calendar[-1] if calendar else None
    return {"state": state, "mode": mode, "as_of": as_of, "cash": state["cash"], "equity": equity,
            "start_date": state.get("start_date"),
            "positions": state["positions"], "orders": state["orders"], "trades": state["trades"],
            "events": state["events"], "equity_curve": state["equity_curve"],
            "daily_marks": state.get("daily_marks", []), "metrics": metrics,
            "calendar_source": calendar_source, "data_as_of": data_as_of,
            "data_stale": data_as_of != as_of,
            "exit_mode": exit_mode, "planned_stop_pct": stop_loss_pct, "planned_target_pct": take_profit_pct,
            "planned_rr": take_profit_pct / stop_loss_pct if exit_mode == "fixed_percent" else None,
            "assumptions": ["收盘信号次交易日开盘成交", "同日双触发先止损",
                            (f"按实际模拟入场价设置 {stop_loss_pct:.1%} 止损 / {take_profit_pct:.1%} 止盈；计划 {take_profit_pct / stop_loss_pct:.2f}R（未扣成本）"
                             if exit_mode == "fixed_percent" else "缺目标仅跟踪止损"),
                            "按字母顺序分配同日现金", "风险预算按价差估算，跳空/费用可能超预算",
                            "上游核验交易日已经完整收盘", "不含分红/拆股处理；不强制期末平仓"]}
