"""Registered-rule 5-minute Paper simulation; no brokerage/order API.

Only completed, exchange-session bars can fill an order. A BUY may enter during
its next actual SPX session, at the first bar open after real registration.
Entry/exit timestamps describe the simulation bar; recorded_at is the caller's
processing cutoff. Source receipts record actual arrival. Daily accounts remain separate.
"""
import copy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from numbers import Real
import os
from pathlib import Path
import re
import tempfile
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo


NY = ZoneInfo("America/New_York")
UTC = timezone.utc
INTERVAL = timedelta(minutes=5)
SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,11}$")
CONFIG_KEYS = ("initial_cash", "risk_fraction", "fee_bps", "slippage_bps", "stop_loss_pct", "take_profit_pct")


def _stamp(value):
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise ValueError("timestamp must be a timezone-aware ISO value") from error
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timestamp must have a timezone")
    return result.astimezone(UTC)


def _iso(value):
    return value.astimezone(UTC).isoformat()


def _day(value):
    return value.astimezone(NY).date().isoformat()


def _finite(value, field, positive=False):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value) or (positive and value <= 0):
        raise ValueError(f"{field} must be a finite {'positive ' if positive else ''}number")
    return float(value)


def _symbol(value):
    if not isinstance(value, str) or not SYMBOL.fullmatch(value.upper()):
        raise ValueError("invalid ticker")
    return value.upper()


def _config(value):
    exit_mode = value.get("exit_mode", "fixed_percent")
    if exit_mode not in ("structural", "fixed_percent"):
        raise ValueError("invalid intraday exit mode")
    if exit_mode == "structural" and any(value.get(key) is not None for key in CONFIG_KEYS[4:]):
        raise ValueError("structural exits do not accept fixed percentages")
    keys = CONFIG_KEYS[:4] if exit_mode == "structural" else CONFIG_KEYS
    config = {key: _finite(value[key], key) for key in keys}
    if (config["initial_cash"] <= 0 or not 0 < config["risk_fraction"] <= 1
            or not 0 <= config["fee_bps"] < 10000 or not 0 <= config["slippage_bps"] < 10000):
        raise ValueError("invalid intraday account configuration")
    if exit_mode == "structural":
        config["exit_mode"] = exit_mode
    elif not 0 < config["stop_loss_pct"] < 1 or not 0 < config["take_profit_pct"] <= 10:
        raise ValueError("invalid intraday account configuration")
    return config


def _signal(value, exit_mode="fixed_percent"):
    signal_day = date.fromisoformat(value["signal_date"]).isoformat()
    observed = value.get("observed_at")
    signal = {"ticker": _symbol(value["ticker"]), "signal_date": signal_day,
              "signal_id": str(value.get("signal_id") or f"{value['ticker']}:{signal_day}"),
              "observed_at": _iso(_stamp(observed)) if observed is not None else None,
              "source_run_id": value.get("source_run_id")}
    if exit_mode == "structural":
        stop = value.get("stop")
        if isinstance(stop, bool) or not isinstance(stop, Real) or not math.isfinite(stop):
            stop = None
        target = value.get("target")
        signal.update(stop=float(stop) if stop is not None else None,
                      target=_finite(target, "target", positive=True) if target is not None else None,
                      risk_plan=copy.deepcopy(value.get("risk_plan")))
    return signal


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _bar(value, now):
    start = _stamp(value["timestamp"])
    session_start, session_end = _stamp(value["session_start"]), _stamp(value["session_end"])
    if (int(start.timestamp()) % 300 or start.microsecond or session_start >= session_end
            or _day(session_start) != _day(session_end)
            or not session_start <= start < session_end or start + INTERVAL > session_end):
        raise ValueError("bar is outside an aligned regular session")
    if start + INTERVAL > now:
        return None
    numbers = {key: _finite(value[key], key, positive=True) for key in ("open", "high", "low", "close")}
    if numbers["high"] < max(numbers["open"], numbers["close"]) or numbers["low"] > min(numbers["open"], numbers["close"]):
        raise ValueError("invalid OHLC bar")
    volume = _finite(value.get("volume", 0), "volume")
    if volume < 0:
        raise ValueError("volume cannot be negative")
    bar = {"timestamp": _iso(start), "end": _iso(start + INTERVAL),
           "session_start": _iso(session_start), "session_end": _iso(session_end),
           **numbers, "volume": volume}
    return {**bar, "content_sha256": _hash(bar), "source_sha256": value.get("source_sha256")}


def _equity(state):
    return state["cash"] + sum(p["quantity"] * p["mark"] for p in state["positions"])


def _event(state, kind, moment, recorded_at, **values):
    state["events"].append({"sequence": len(state["events"]) + 1, "kind": kind,
                            "date": moment, "timestamp": moment, "recorded_at": recorded_at, **values})


def _skip(state, order, reason, now):
    order["status"], order["reason"] = "skipped", reason
    _event(state, "skipped", now, now, signal_id=order["signal_id"], ticker=order["ticker"], reason=reason)


def _initial_risk(position):
    position.setdefault("initial_stop", position["stop"])
    position.setdefault("initial_risk_per_share", position["entry_price"] - position["initial_stop"])
    position.setdefault("stop_adjustments", [])


def _trailing_policy(state, value, now):
    if value is None:
        return state.get("trailing_policy")
    if state["config"].get("exit_mode") != "structural":
        raise ValueError("trailing policy requires structural exits")
    policy = copy.deepcopy(value)
    if not isinstance(policy, dict) or not isinstance(policy.get("version"), str) or not policy["version"]:
        raise ValueError("trailing policy must identify its version")
    policy["enabled_at"] = _iso(_stamp(policy["enabled_at"]))
    if _stamp(policy["enabled_at"]) > now:
        raise ValueError("trailing policy cannot be enabled in the future")
    if state.get("trailing_policy") is not None:
        if state["trailing_policy"] != policy:
            raise ValueError("trailing policy cannot change on resume")
        return policy
    if state.get("last_checked_at") and _stamp(policy["enabled_at"]) < _stamp(state["last_checked_at"]):
        raise ValueError("trailing policy cannot backdate its activation")
    state.update(trailing_policy=policy, stop_updates=[], stop_adjustments=[], processed_stop_updates={})
    for position in state["positions"]:
        _initial_risk(position)
    _event(state, "trailing_enabled", policy["enabled_at"], _iso(now), policy_version=policy["version"], policy=policy)
    return policy


def _stop_update(value, policy, now):
    identity = value.get("update_id")
    signal_id = value.get("source_signal_id")
    if not isinstance(identity, str) or not identity or not isinstance(signal_id, str) or not signal_id:
        raise ValueError("stop update requires update_id and source_signal_id")
    if value.get("policy_version") != policy["version"]:
        raise ValueError("stop update policy version does not match")
    day = date.fromisoformat(value["source_as_of"]).isoformat()
    completed, observed, effective = (_stamp(value[key]) for key in ("source_completed_at", "observed_at", "effective_at"))
    if _day(completed) != day or not completed <= observed <= effective or observed > now:
        raise ValueError("stop update evidence is future or inconsistent")
    reason, evidence = value.get("reason"), value.get("evidence")
    if not isinstance(reason, str) or not reason or not isinstance(evidence, dict):
        raise ValueError("stop update requires a reason and evidence")
    return {"update_id": identity, "ticker": _symbol(value["ticker"]), "source_signal_id": signal_id,
            "new_stop": _finite(value["new_stop"], "new stop", positive=True), "source_as_of": day,
            "source_completed_at": _iso(completed), "observed_at": _iso(observed), "effective_at": _iso(effective),
            "policy_version": policy["version"], "reason": reason, "evidence": copy.deepcopy(evidence)}


def _register_stop_updates(state, values, policy, now):
    if values and policy is None:
        raise ValueError("stop updates require an enabled trailing policy")
    for value in values or []:
        update = _stop_update(value, policy, now)
        identity, fingerprint = update["update_id"], _hash(update)
        if identity in state["processed_stop_updates"]:
            if state["processed_stop_updates"][identity] != fingerprint:
                raise ValueError("update_id already exists with different content")
            continue
        position = next((p for p in state["positions"] if p["ticker"] == update["ticker"]
                         and p["source_signal_id"] == update["source_signal_id"]), None)
        if position is not None and update["source_as_of"] < position["entry_date"]:
            raise ValueError("stop update source predates the position entry")
        state["processed_stop_updates"][identity] = fingerprint
        state["stop_updates"].append({**update, "status": "pending" if position is not None else "position_closed",
                                      "registered_at": _iso(now),
                                      **({"status_reason": "position_closed_before_activation", "evaluated_at": _iso(now)} if position is None else {})})


def _closed_stop_updates(state, now):
    held = {(p["ticker"], p["source_signal_id"]) for p in state["positions"]}
    for update in state.get("stop_updates", []):
        if update["status"] == "pending" and (update["ticker"], update["source_signal_id"]) not in held:
            update.update(status="position_closed", status_reason="position_closed_before_activation", evaluated_at=now)


def _apply_stop_updates(state, group, moment, now, fees, slip):
    policy = state.get("trailing_policy")
    if policy is None:
        return
    _closed_stop_updates(state, now)
    for update in sorted(state["stop_updates"], key=lambda u: (u["effective_at"], u["observed_at"], u["update_id"])):
        if update["status"] != "pending":
            continue
        value = group.get(update["ticker"])
        if (value is None or value["volume"] <= 0
                or _stamp(moment) < max(_stamp(update["effective_at"]), _stamp(update["observed_at"]), _stamp(policy["enabled_at"]))
                or _day(_stamp(moment)) <= update["source_as_of"]):
            continue
        position = next((p for p in state["positions"] if p["ticker"] == update["ticker"]
                         and p["source_signal_id"] == update["source_signal_id"]), None)
        if position is None:
            update.update(status="position_closed", status_reason="position_closed_before_activation", evaluated_at=now)
            continue
        if update["new_stop"] <= position["stop"]:
            update.update(status="no_change", status_reason="not_higher", old_stop=position["stop"], evaluated_at=now, evaluated_effective_at=moment)
            continue
        _initial_risk(position)
        adjustment = {k: copy.deepcopy(v) for k, v in update.items() if k not in ("status", "registered_at")}
        adjustment.update(old_stop=position["stop"], requested_effective_at=update["effective_at"], effective_at=moment,
                          applied_at=now, recorded_at=now)
        position["stop"] = update["new_stop"]
        position["current_stop_update_id"] = update["update_id"]
        position["stop_adjustments"].append(copy.deepcopy(adjustment))
        state["stop_adjustments"].append(adjustment)
        update.update(status="applied", old_stop=adjustment["old_stop"], applied_at=now, applied_effective_at=moment)
        _event(state, "stop_adjustment", moment, now, ticker=position["ticker"], signal_id=position["source_signal_id"],
               update_id=update["update_id"], old_stop=adjustment["old_stop"], new_stop=adjustment["new_stop"],
               reason=update["reason"], adjustment=copy.deepcopy(adjustment))
        if value["open"] <= position["stop"]:
            _close(state, position, value["open"], value, "trailing_stop_gap", now, fees, slip)
    _closed_stop_updates(state, now)


def _close(state, position, raw_price, bar, reason, now, fees, slip):
    price = raw_price * (1 - slip)
    proceeds = position["quantity"] * price
    exit_fee = proceeds * fees
    state["cash"] += proceeds - exit_fee
    state["fees_paid"] += exit_fee
    moment = bar["timestamp"] if reason.endswith("_gap") else bar["end"]
    net = proceeds - position["quantity"] * position["entry_price"] - position["entry_fee"] - exit_fee
    risk = position["quantity"] * position.get("initial_risk_per_share", position["entry_price"] - position["stop"])
    trade = {**position, "exit_date": _day(_stamp(moment)), "exit_timestamp": moment,
             "exit_price": price, "exit_fee": exit_fee, "exit_reason": reason,
             "net_pnl": net, "pnl_pct": net / (position["quantity"] * position["entry_price"] + position["entry_fee"]),
             "realized_r": net / risk, "price_bar": bar, "recorded_at": now}
    state["trades"].append(trade)
    state["positions"].remove(position)
    _event(state, "exit", moment, now, signal_id=position["source_signal_id"], ticker=position["ticker"],
           quantity=position["quantity"], price=price, fee=exit_fee, reason=reason, price_bar=bar,
           **({"risk_plan": position["risk_plan"]} if position.get("exit_mode") == "structural" else {}))


def apply_intraday(signals, bars, *, now, config, state=None, started_at=None, stop_updates=None, trailing_policy=None):
    """Queue BUYs and apply complete 5-minute bars to a separate Paper account.

SPX bars determine actual next-session dates. Without fresh SPX bars no new
position opens; protective exits on valid held-stock bars remain available.
Late registrations enter only a later bar of the exact next session. Old or
expired signals never turn into a trade several days later. The caller must
verify upstream that the signal itself used completed daily data.
"""
    now_stamp = _stamp(now)
    now_text = _iso(now_stamp)
    today = _day(now_stamp)
    config = _config(config)
    exit_mode = config.get("exit_mode", "fixed_percent")
    if state is None:
        registration = _stamp(started_at) if started_at is not None else now_stamp
        if registration > now_stamp:
            raise ValueError("account registration cannot be in the future")
        state = {"schema_version": 1, "execution_model": "intraday_5m", "mode": "forward",
                 "config": config, "started_at": _iso(registration), "start_date": _day(registration),
                 "cash": config["initial_cash"], "fees_paid": 0.0, "positions": [], "orders": [],
                 "trades": [], "events": [], "equity_curve": [], "processed_signals": {},
                 "last_bar_at": {}, "last_data_at": None, "last_session_end": None, "last_checked_at": None,
                 "coverage_gaps": {}}
    else:
        state = copy.deepcopy(state.get("state", state))
        if (state.get("schema_version") != 1 or state.get("execution_model") != "intraday_5m"
                or state.get("config") != config):
            raise ValueError("intraday account configuration cannot change on resume")
        if state["last_checked_at"] is not None and _stamp(state["last_checked_at"]) > now_stamp:
            raise ValueError("now cannot move backwards")
        registration = _stamp(state["started_at"])
    policy = _trailing_policy(state, trailing_policy, now_stamp)
    _register_stop_updates(state, stop_updates, policy, now_stamp)
    state.setdefault("coverage_gaps", {})
    entry_watermark = _stamp(state["last_data_at"]) if state.get("last_data_at") else registration
    normalized = {}
    for ticker, values in bars.items():
        ticker = _symbol(ticker)
        parsed = [candidate for value in values if (candidate := _bar(value, now_stamp)) is not None]
        if len({b["timestamp"] for b in parsed}) != len(parsed):
            raise ValueError("duplicate intraday bar timestamp")
        normalized[ticker] = sorted(parsed, key=lambda b: b["timestamp"])
    for value in sorted((_signal(v, exit_mode) for v in signals), key=lambda v: (v["signal_date"], v["ticker"], v["signal_id"])):
        if value["signal_date"] > today:
            continue
        identity = value["signal_id"]
        fingerprint = _hash({k: v for k, v in value.items() if k != "source_run_id"})
        if identity in state["processed_signals"]:
            if state["processed_signals"][identity] != fingerprint:
                raise ValueError("signal_id already exists with different content")
            continue
        state["processed_signals"][identity] = fingerprint
        order = {**value, "status": "pending", "reason": None, "entry_session": None, "exit_mode": exit_mode}
        if exit_mode == "fixed_percent":
            order.update(stop_loss_pct=config["stop_loss_pct"], take_profit_pct=config["take_profit_pct"],
                         planned_rr=config["take_profit_pct"] / config["stop_loss_pct"])
        else:
            order.update(planned_stop_pct=None, planned_target_pct=None, planned_rr=None)
        state["orders"].append(order)
        if value["observed_at"] is None:
            _skip(state, order, "missing_observed_at", now_text)
        elif exit_mode == "structural" and (value["stop"] is None or value["stop"] <= 0):
            _skip(state, order, "invalid_stop", now_text)
    reference = normalized.get("SPX", [])
    session_days = sorted({_day(_stamp(b["timestamp"])) for b in reference})
    reference_fresh = False
    if reference and _day(_stamp(reference[-1]["session_start"])) == today:
        expected = min(datetime.fromtimestamp(int(now_stamp.timestamp()) // 300 * 300, UTC), _stamp(reference[-1]["session_end"]))
        reference_fresh = (_stamp(reference[-1]["session_start"]) <= now_stamp
                           and expected - _stamp(reference[-1]["end"]) <= timedelta(minutes=10))
    for order in state["orders"]:
        if order["status"] != "pending":
            continue
        if not reference_fresh:
            order["reason"] = "calendar_unavailable"
            continue
        if order["signal_date"] < session_days[0]:
            _skip(state, order, "stale_signal", now_text)
            continue
        if order["signal_date"] <= session_days[-1] and order["signal_date"] not in session_days:
            _skip(state, order, "missing_signal_session", now_text)
            continue
        entry = next((day for day in session_days if day > order["signal_date"]), None)
        order["entry_session"] = entry
        if entry is not None and entry < today:
            _skip(state, order, "signal_expired", now_text)
        else:
            order["reason"] = "waiting_next_bar" if entry else "waiting_next_session"
    groups = {}
    for ticker, values in normalized.items():
        for value in values:
            if value["timestamp"] <= state["last_bar_at"].get(ticker, ""):
                continue
            if _stamp(value["end"]) < registration:
                continue
            groups.setdefault(value["timestamp"], {})[ticker] = value
    fees, slip = config["fee_bps"] / 10000, config["slippage_bps"] / 10000
    for moment, group in sorted(groups.items()):
        # Missing held-stock intervals can contain unseen exits. Keep the ticker
        # watermark unchanged so a later complete fetch can reconcile them.
        for position in state["positions"]:
            ticker = position["ticker"]
            value = group.get(ticker)
            if value is None:
                continue
            expected = _stamp(position["mark_timestamp"])
            opening = _stamp(value["timestamp"])
            next_session = next((day for day in session_days if day > _day(expected)), None)
            overnight = (position.get("mark_session_end") == _iso(expected)
                         and _day(expected) in session_days
                         and value["timestamp"] == value["session_start"]
                         and _day(opening) == next_session)
            if value["volume"] <= 0 or (opening != expected and not overnight):
                state["coverage_gaps"][ticker] = {"reason": "intraday_coverage_gap",
                    "expected_from": _iso(expected), "available_from": value["timestamp"]}
                position.update(stale=True, data_error="intraday_coverage_gap")
                del group[ticker]
            else:
                state["coverage_gaps"].pop(ticker, None)
                position.pop("data_error", None)
        if not group:
            continue
        # All known opening prices, then gap exits, then deterministic new-entry allocation.
        for position in list(state["positions"]):
            value = group.get(position["ticker"])
            if value is None or value["volume"] <= 0:
                continue
            position["mark"] = value["open"]
            if value["open"] <= position["stop"]:
                _close(state, position, value["open"], value, "trailing_stop_gap" if position.get("current_stop_update_id") else "stop_gap", now_text, fees, slip)
            elif position["target"] is not None and value["open"] >= position["target"]:
                _close(state, position, value["open"], value, "target_gap", now_text, fees, slip)
        _apply_stop_updates(state, group, moment, now_text, fees, slip)
        for order in sorted(state["orders"], key=lambda o: (o["signal_date"], o["ticker"], o["signal_id"])):
            value = group.get(order["ticker"])
            if (order["status"] != "pending" or not reference_fresh or value is None
                    or order["entry_session"] != _day(_stamp(moment)) or value["volume"] <= 0):
                continue
            if _stamp(moment) < max(registration, entry_watermark, _stamp(order["observed_at"])):
                continue
            if any(p["ticker"] == order["ticker"] for p in state["positions"]):
                _skip(state, order, "already_holding", now_text)
                continue
            price = value["open"] * (1 + slip)
            stop = price * (1 - config["stop_loss_pct"]) if exit_mode == "fixed_percent" else order["stop"]
            target = price * (1 + config["take_profit_pct"]) if exit_mode == "fixed_percent" else order["target"]
            if stop >= value["open"]:
                _skip(state, order, "invalid_stop", now_text)
                continue
            if target is not None and target <= price:
                _skip(state, order, "target_at_or_below_entry", now_text)
                continue
            stop_pct = config["stop_loss_pct"] if exit_mode == "fixed_percent" else (price - stop) / price
            target_pct = config["take_profit_pct"] if exit_mode == "fixed_percent" else (target / price - 1 if target is not None else None)
            planned_rr = target_pct / stop_pct if target_pct is not None else None
            quantity = min(math.floor(_equity(state) * config["risk_fraction"] / (price - stop)),
                           math.floor(state["cash"] / (price * (1 + fees))))
            if quantity <= 0:
                _skip(state, order, "insufficient_budget", now_text)
                continue
            entry_fee = quantity * price * fees
            state["cash"] -= quantity * price + entry_fee
            state["fees_paid"] += entry_fee
            position = {"ticker": order["ticker"], "quantity": quantity, "entry_price": price,
                        "entry_date": _day(_stamp(moment)), "entry_timestamp": moment, "entry_fee": entry_fee,
                        "stop": stop, "target": target, "stop_loss_pct": stop_pct,
                        "take_profit_pct": target_pct, "planned_rr": planned_rr,
                        "exit_mode": exit_mode, "mark": price, "mark_date": _day(_stamp(moment)),
                        "mark_timestamp": moment, "stale": False, "source_signal_id": order["signal_id"],
                        "mark_session_end": value["session_end"], "source_run_id": order.get("source_run_id")}
            if exit_mode == "structural":
                position.update(planned_stop_pct=stop_pct, planned_target_pct=target_pct, risk_plan=order["risk_plan"])
                if policy is not None:
                    _initial_risk(position)
            state["positions"].append(position)
            order["status"], order["reason"] = "filled", None
            order["entry_timestamp"], order["entry_price"] = moment, price
            order["stop"], order["target"] = stop, target
            if exit_mode == "structural":
                order.update(planned_stop_pct=stop_pct, planned_target_pct=target_pct, planned_rr=planned_rr)
            _event(state, "entry", moment, now_text, ticker=order["ticker"], signal_id=order["signal_id"],
                   quantity=quantity, price=price, fee=entry_fee, price_bar=value,
                   **({"risk_plan": order["risk_plan"], "stop": stop, "target": target, "exit_mode": exit_mode,
                       "planned_rr": planned_rr} if exit_mode == "structural" else {}))
        for position in list(state["positions"]):
            value = group.get(position["ticker"])
            if value is None or value["volume"] <= 0:
                continue
            stop_hit = value["low"] <= position["stop"]
            target_hit = position["target"] is not None and value["high"] >= position["target"]
            if stop_hit:
                reason = "stop_and_target" if target_hit else "stop"
                if position.get("current_stop_update_id"):
                    reason = "trailing_" + reason
                _close(state, position, position["stop"], value, reason, now_text, fees, slip)
            elif target_hit:
                _close(state, position, position["target"], value, "target", now_text, fees, slip)
            else:
                position.update(mark=value["close"], mark_date=_day(_stamp(value["end"])),
                                mark_timestamp=value["end"], mark_session_end=value["session_end"])
        ending = max(value["end"] for value in group.values())
        for ticker, value in group.items():
            state["last_bar_at"][ticker] = value["timestamp"]
        state["last_data_at"] = max(state.get("last_data_at") or "", ending)
        state["last_session_end"] = max(state.get("last_session_end") or "", *(value["session_end"] for value in group.values()))
        for position in state["positions"]:
            position["stale"] = bool(position.get("data_error")) or position["mark_timestamp"] < ending
        point = {"date": state["last_data_at"], "price_data_as_of": ending, "recorded_at": now_text,
                 "cash": state["cash"], "equity": _equity(state),
                 "stale_positions": sum(p["stale"] for p in state["positions"])}
        if state["equity_curve"] and state["equity_curve"][-1]["date"] == point["date"]:
            state["equity_curve"][-1] = point
        else:
            state["equity_curve"].append(point)
    _closed_stop_updates(state, now_text)
    state["last_checked_at"] = now_text
    equity = _equity(state)
    latest = state.get("last_data_at")
    realized = sum(t["net_pnl"] for t in state["trades"])
    unrealized = sum(p["quantity"] * (p["mark"] - p["entry_price"]) - p["entry_fee"] for p in state["positions"])
    peak, drawdown = config["initial_cash"], 0.0
    for point in state["equity_curve"]:
        peak = max(peak, point["equity"])
        drawdown = min(drawdown, point["equity"] / peak - 1)
    expected_data_end = now_stamp
    if state.get("last_session_end") and _day(_stamp(state["last_session_end"])) == today:
        expected_data_end = min(datetime.fromtimestamp(int(now_stamp.timestamp()) // 300 * 300, UTC), _stamp(state["last_session_end"]))
    for position in state["positions"]:
        if expected_data_end - _stamp(position["mark_timestamp"]) > timedelta(minutes=10):
            position["stale"] = True
    data_stale = latest is None or expected_data_end - _stamp(latest) > timedelta(minutes=10) or any(p["stale"] for p in state["positions"])
    metrics = {"realized_pnl": realized, "unrealized_pnl": unrealized, "fees_paid": state["fees_paid"],
               "total_return": equity / config["initial_cash"] - 1, "max_drawdown": drawdown,
               "closed_trades": len(state["trades"]), "open_positions": len(state["positions"]),
               "stale_positions": sum(p["stale"] for p in state["positions"]),
               "win_rate": sum(t["net_pnl"] > 0 for t in state["trades"]) / len(state["trades"]) if state["trades"] else None}
    return {"state": state, "execution_model": "intraday_5m", "mode": "forward", "as_of": today,
            "exit_mode": exit_mode, "stop_loss_pct": config.get("stop_loss_pct"), "take_profit_pct": config.get("take_profit_pct"),
            "planned_rr": config["take_profit_pct"] / config["stop_loss_pct"] if exit_mode == "fixed_percent" else None, "start_date": state["start_date"],
            "started_at": state["started_at"], "last_checked_at": now_text, "data_as_of": latest, "data_stale": data_stale,
            "wallclock_age_seconds": (now_stamp - _stamp(latest)).total_seconds() if latest else None,
            "cash": state["cash"], "equity": equity, "config": config, "coverage_gaps": state["coverage_gaps"],
            **{key: state[key] for key in ("positions", "orders", "trades", "events", "equity_curve")}, "metrics": metrics,
            **({key: state[key] for key in ("trailing_policy", "stop_updates", "stop_adjustments")} if policy is not None else {}),
            "assumptions": ["真实完整5分钟K线模拟，不是交易所逐笔成交", "BUY只在下一实际交易日首次登记后的K线开盘模拟入场",
                            "登记前不补买；超过下一交易日的信号作废",
                            ("初始止损/目标来自信号日结构快照；移动止损只按已登记的完整日线决策上移，R仍以初始风险计算" if policy is not None
                             else "止损/目标取自信号日结构快照，成交后按实际价差计算仓位与RR；不自动放宽止损") if exit_mode == "structural" else "止损/目标按实际模拟入场价冻结",
                            "跳空按开盘，单根K线双触发先止损", "触发时刻仅精确到5分钟K线，保留核验批次截止时间与行情接收证据",
                            "持仓缺失中间K线暂停该标的，补齐后核对；不以之后的价格推测缺失区间",
                            "价差风险预算不包含跳空与费用；不含分红/拆股会计"]}


def _periods(value):
    if isinstance(value, list):
        return [period for item in value for period in _periods(item)]
    if isinstance(value, dict) and "start" in value and "end" in value:
        start, end = value["start"], value["end"]
        if type(start) is not int or type(end) is not int or start >= end:
            raise ValueError("invalid provider session period")
        return [(datetime.fromtimestamp(start, UTC), datetime.fromtimestamp(end, UTC))]
    return []


def _parse_response(raw, ticker, provider_symbol, now):
    data = json.loads(raw)
    chart = data.get("chart") if isinstance(data, dict) else None
    if not isinstance(chart, dict) or chart.get("error") is not None:
        raise ValueError("Yahoo intraday response reports an error")
    result = chart.get("result")
    if not isinstance(result, list) or len(result) != 1:
        raise ValueError("Yahoo intraday response must contain one result")
    item = result[0]
    meta = item["meta"]
    if (meta.get("symbol") != provider_symbol or meta.get("currency") != "USD"
            or meta.get("exchangeTimezoneName") not in ("America/New_York", "US/Eastern")
            or meta.get("dataGranularity") != "5m"):
        raise ValueError("Yahoo intraday source identity does not match request")
    periods = _periods(meta.get("tradingPeriods"))
    regular = _periods(meta.get("currentTradingPeriod", {}).get("regular"))
    fallback = not periods
    periods = periods or regular
    if not periods or not regular:
        raise ValueError("Yahoo response does not provide actual regular sessions")
    timestamps = item["timestamp"]
    quotes = item["indicators"]["quote"]
    if not isinstance(timestamps, list) or not isinstance(quotes, list) or len(quotes) != 1:
        raise ValueError("Yahoo intraday OHLCV arrays are missing")
    quote = quotes[0]
    keys = ("open", "high", "low", "close", "volume")
    if any(not isinstance(quote.get(key), list) or len(quote[key]) != len(timestamps) for key in keys):
        raise ValueError("Yahoo intraday OHLCV arrays do not align")
    raw_hash = hashlib.sha256(raw).hexdigest()
    bars, excluded = [], 0
    for index, stamp in enumerate(timestamps):
        if type(stamp) is not int:
            raise ValueError("Yahoo intraday timestamp must be an integer")
        start = datetime.fromtimestamp(stamp, UTC)
        values = {key: quote[key][index] for key in keys}
        session = next((p for p in periods if p[0] <= start and start + INTERVAL <= p[1]), None)
        if stamp % 300 or start + INTERVAL > now or session is None or any(v is None for v in values.values()):
            excluded += 1
            continue
        candidate = _bar({"timestamp": _iso(start), "session_start": _iso(session[0]), "session_end": _iso(session[1]),
                          **values, "source_sha256": raw_hash}, now)
        bars.append(candidate)
    bars.sort(key=lambda b: b["timestamp"])
    if len({b["timestamp"] for b in bars}) != len(bars):
        raise ValueError("Yahoo intraday response has duplicate aligned bars")
    session_start, session_end = regular[0]
    expected_end = min(datetime.fromtimestamp(int(now.timestamp()) // 300 * 300, UTC), session_end)
    current = [b for b in bars if _stamp(b["session_start"]) == session_start]
    latest = current[-1]["end"] if current else None
    delay = max(0.0, (expected_end - _stamp(latest)).total_seconds()) if latest else None
    stale = (_day(session_start) != _day(now) or now < session_start
             or (expected_end > session_start and (latest is None or delay > 600)))
    events = item.get("events")
    splits = []
    if isinstance(events, dict):
        raw_splits = events.get("splits", {})
        if not isinstance(raw_splits, dict):
            raise ValueError("Yahoo splits must be an object")
        for event in raw_splits.values():
            if not isinstance(event, dict) or type(event.get("date")) is not int:
                raise ValueError("Yahoo split event is invalid")
            moment = datetime.fromtimestamp(event["date"], UTC)
            if moment > now:
                continue
            splits.append({"date": _day(moment), "timestamp": event["date"],
                           "numerator": _finite(event.get("numerator"), "split numerator", positive=True),
                           "denominator": _finite(event.get("denominator"), "split denominator", positive=True),
                           "split_ratio": event.get("splitRatio")})
        splits.sort(key=lambda item: item["timestamp"])
    source = {"ticker": ticker, "provider": "Yahoo Finance chart", "interval": "5m", "check_as_of": _iso(now),
              "regular_start": _iso(session_start), "regular_end": _iso(session_end), "raw_sha256": raw_hash,
              "session_start": _iso(session_start), "session_end": _iso(session_end),
              "latest_completed_at": latest, "delay_seconds": delay, "stale": bool(stale),
              "wallclock_age_seconds": (now - _stamp(latest)).total_seconds() if latest else None,
              "exchange_delay_seconds": None, "provider_exchange_data_delayed_by": meta.get("exchangeDataDelayedBy"),
              "splits": splits, "corporate_events_available": isinstance(events, dict), "excluded_rows": excluded,
              "session_metadata_fallback": fallback, "raw_response_retained": False}
    return ([] if stale or latest is None else bars), source


def _archive(path, value, *, write_once=False):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".quote-", delete=False) as output:
            temporary = Path(output.name)
            output.write(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode())
        if write_once:
            try:
                os.link(temporary, path)
            except FileExistsError:
                pass
        else:
            os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def fetch_yahoo_intraday(tickers, *, now=None, cache_dir=None):
    """Return actual completed bars and source freshness for stocks plus SPX.

The source sometimes appends an irregular latest-price row and a null forming
bucket; neither is a complete 5-minute bar. Actual exchange tradingPeriods,
including early closes, bound each bar. Exchange delay is unknown when absent.
Compact receipts retain source-response hashes; normalized bars are archived
once per content hash rather than repeating entire five-day responses. Trade
events retain their actual OHLC evidence.
"""
    now = _stamp(now) if now is not None else datetime.now(UTC)
    result = {"bars": {}, "sources": {}, "errors": {}}
    for ticker in sorted({_symbol(value) for value in tickers} | {"SPX"}):
        provider_symbol = "^GSPC" if ticker == "SPX" else ticker.replace(".", "-")
        symbol = urllib.parse.quote(provider_symbol, safe="")
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5d&interval=5m&includePrePost=false&events=div%2Csplits"
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read()
            received_at = _iso(datetime.now(UTC))
            bars, source = _parse_response(raw, ticker, provider_symbol, now)
            source["received_at"] = received_at
            result["bars"][ticker], result["sources"][ticker] = bars, source
            if cache_dir is not None:
                for value in bars:
                    stamp = value["timestamp"].replace(":", "").replace("+", "")
                    bar_path = Path(cache_dir) / "bars" / _day(_stamp(value["timestamp"])) / f"{ticker}_{stamp}_{value['content_sha256']}.json"
                    if not bar_path.exists():
                        _archive(bar_path, {"ticker": ticker, "received_at": received_at, "raw_sha256": source["raw_sha256"],
                                            "bar": value}, write_once=True)
                latest = bars[-1] if bars else None
                suffix = (source["latest_completed_at"] or _iso(now)).replace(":", "").replace("+", "")
                path = Path(cache_dir) / _day(now) / f"{ticker}_{suffix}_{source['raw_sha256'][:12]}.json"
                _archive(path, {**source, "latest_bar": latest})
        except (ValueError, KeyError, TypeError, IndexError, OSError) as error:
            result["bars"].pop(ticker, None)
            result["sources"].pop(ticker, None)
            result["errors"][ticker] = str(error)[:300]
    return result
