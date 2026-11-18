"""Versioned Paper retest plans and consistent, confluent structure protection.

The three-R net threshold, one-ATR proximity, daily confirmation lag and cost
model are explicit research assumptions, not a verbatim video algorithm.
"""
import copy
from datetime import date
from decimal import Decimal
import hashlib
import json

import pandas as pd

from .config import Config
from .indicators import atr, ema, sma, swing_pivots
from .trailing import _buffered, _completed, _number, _stamp


POLICY_VERSION = "structural_retest_v2"
ENTRY_POLICY = {"strategy_version": POLICY_VERSION, "version": POLICY_VERSION, "minimum_net_rr": 3,
                "required_support": "ma_zone_and_structure",
                "buffer_atr": .1, "support_proximity_atr": 1,
                "support_cluster_atr": 2, "confirmation": "previous_completed_5m_retest_reclaim",
                "description": "买入前重新确认同次回踩与扣成本后至少3R；初始和后续均采用近端结构与支撑共振下方保护"}
POLICY = {"policy_version": POLICY_VERSION, "timeframe": "1d", "ma_period": 20,
          "atr_period": 14, "pivot_k": 3, "buffer_atr": .1, "minimum_raise": .01,
          "selection": "confirmed_post_entry_higher_low_and_ma_confluence", "rounding": "floor_cent",
          "description": "仅入场后确认更高低点且与20日均线区共振才上移；禁止孤立均线切换旧结构",
          "viewpoints": ["E1 [44:53–45:32]", "F2b [50:53–51:26]", "G1/G2"]}
COLUMNS = ("Open", "High", "Low", "Close", "Volume")


def _daily(frame, source_as_of, observed_at, entry_date=None):
    day, observed = date.fromisoformat(source_as_of), _stamp(observed_at)
    if observed < _completed(day):
        raise ValueError("structure source session is incomplete")
    if not isinstance(frame, pd.DataFrame) or not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError("structure prices require a daily DatetimeIndex")
    if not all(column in frame for column in COLUMNS):
        raise ValueError("structure prices require OHLCV")
    used = frame.loc[:source_as_of, list(COLUMNS)].copy()
    if (len(used) < 21 or used.index[-1].date() != day or not used.index.is_monotonic_increasing
            or not used.index.normalize().is_unique):
        raise ValueError("structure prices require requested day and at least 21 unique daily bars")
    for o, high, low, close, volume in used.itertuples(index=False, name=None):
        o, high, low, close = [_number(value, "OHLC", positive=True) for value in (o, high, low, close)]
        if _number(volume, "volume") < 0 or high < max(o, close) or low > min(o, close):
            raise ValueError("invalid OHLCV in structure prices")
    events = frame.attrs.get("events") or {}
    if not isinstance(events, dict):
        raise ValueError("invalid split metadata")
    splits = frame.attrs.get("splits", events.get("splits", []))
    if not isinstance(splits, list):
        raise ValueError("invalid split metadata")
    for event in splits:
        try:
            split_day = date.fromisoformat(event["date"])
        except (ValueError, TypeError, KeyError) as error:
            raise ValueError("invalid split metadata") from error
        if entry_date and date.fromisoformat(entry_date) < split_day <= day:
            raise ValueError("split during holding requires corporate-action accounting")
    used = used.astype(float)
    fingerprint = hashlib.sha256(used.to_csv(index_label="Date", date_format="%Y-%m-%d").encode()).hexdigest()
    return used, fingerprint, observed


def entry_price_check(risk_plan, raw_price, *, fee_bps, slippage_bps):
    """Recheck the actual executable opening price and both-side net R."""
    raw = _number(raw_price, "entry opening", positive=True)
    fees, slip = _number(fee_bps, "fee bps") / 10000, _number(slippage_bps, "slippage bps") / 10000
    if not 0 <= fees < 1 or not 0 <= slip < 1:
        raise ValueError("invalid execution costs")
    result = {"eligible": False, "reason": risk_plan.get("reason") or "entry_plan_unavailable",
              "entry_price": raw * (1 + slip), "net_rr": None,
              "net_risk_per_share": None, "net_reward_per_share": None}
    if not risk_plan.get("eligible"):
        return result
    stop, target = risk_plan["stop"], risk_plan["target"]
    entry = result["entry_price"]
    lower = risk_plan["support_band"][0]
    if stop is None or raw <= stop:
        result["reason"] = "invalid_stop"
    elif raw < lower:
        result["reason"] = "entry_below_support"
    elif target is None or target <= entry:
        result["reason"] = "target_at_or_below_entry"
    elif entry > risk_plan["maximum_entry_price"]:
        result["reason"] = "entry_above_limit"
    else:
        entry_cost = entry * (1 + fees)
        risk = entry_cost - stop * (1 - slip) * (1 - fees)
        reward = target * (1 - slip) * (1 - fees) - entry_cost
        ratio = reward / risk
        result.update(net_rr=ratio, net_risk_per_share=risk, net_reward_per_share=reward)
        if ratio < risk_plan["minimum_net_rr"]:
            result["reason"] = "entry_rr_below_minimum"
        else:
            result.update(eligible=True, reason="entry_price_confirmed")
    return result


def build_entry_plan(report, frame, *, source_as_of, observed_at, source_run_id=None,
                     fee_bps=5, slippage_bps=5):
    """Freeze this pullback's own anchor and support band before any entry."""
    used, fingerprint, observed = _daily(frame, source_as_of, observed_at)
    if report.get("as_of") != source_as_of:
        raise ValueError("report and structure source date do not match")
    atr_value = _number(atr(used, 14).iloc[-1], "ATR", positive=True)
    close = _number(report["close"], "signal close", positive=True)
    target = report.get("target")
    risk = {**ENTRY_POLICY, "eligible": False, "reason": "entry_plan_unavailable",
            "basis": "confluent_pullback_structure", "signal_date": source_as_of,
            "source_as_of": source_as_of, "source_run_id": source_run_id,
            "observed_at": observed.isoformat(), "source_completed_at": _completed(date.fromisoformat(source_as_of)).isoformat(),
            "source_fingerprint": fingerprint, "source_snapshots": copy.deepcopy(frame.attrs.get("snapshots") or []),
            "atr": atr_value, "buffer_value": atr_value * .1, "target": target,
            "stop": None, "support_band": [], "support_levels": {}, "ma_zone_level": None, "anchor_date": None,
            "anchor_price": None, "reference_net_rr": None, "report_close": close,
            "policy_assumption": "项目假设：本次动能峰后回调低点与成熟均线区及另一类成熟结构成带，1ATR近端容差，0.1ATR缓冲；扣双边成本至少3R，实际成交前再确认"}

    def finish(reason, eligible=False):
        risk.update(reason=reason, eligible=eligible)
        return {"eligible": eligible, "reason": reason, "risk_plan": risk,
                "stop": risk["stop"], "target": target}

    if report.get("verdict") != "BUY":
        return finish("signal_not_buy")
    age = report.get("pullback_age")
    if not isinstance(age, int) or isinstance(age, bool) or not 1 <= age <= Config().first_pullback_max_days or age >= len(used):
        return finish("missing_pullback")
    levels = {key: _number(report["edges"][key]["level"], "support level", positive=True)
              for key in ("ma_zone", "sr_flip", "trendline")
              if key in report.get("structural_mature", []) and report.get("edges", {}).get(key, {}).get("level") is not None}
    risk["support_levels"] = levels
    risk["ma_zone_level"] = levels.get("ma_zone")
    if "ma_zone" not in levels:
        return finish("missing_ma_confluence")
    if len(levels) < 2:
        return finish("insufficient_confluence")
    lower, upper = min(levels.values()), max(levels.values())
    risk.update(support_band=[lower, upper], maximum_entry_price=upper + atr_value,
                confirmation_close=upper, retest_ceiling=upper + risk["buffer_value"])
    if upper - lower > 2 * atr_value:
        return finish("support_band_too_wide")
    pullback = used.iloc[-age:]
    anchor = float(pullback["Low"].min())
    anchor_day = pullback.index[pullback["Low"] == anchor][-1].date().isoformat()
    risk.update(anchor_date=anchor_day, anchor_price=anchor, stop_anchor=anchor)
    if max(lower - anchor, anchor - upper, 0) > atr_value:
        return finish("anchor_outside_support")
    if close < lower:
        return finish("source_below_support")
    if close > risk["maximum_entry_price"]:
        return finish("source_above_entry_limit")
    risk["stop"] = _buffered(min(anchor, lower), atr_value, .1)
    if risk["stop"] <= 0 or risk["stop"] >= close:
        return finish("invalid_stop")
    if target is None:
        return finish("missing_target")
    target = _number(target, "target", positive=True)
    risk["target"] = target
    if target <= max(close, risk["stop"]):
        return finish("invalid_target")
    risk["eligible"] = True
    check = entry_price_check(risk, close, fee_bps=fee_bps, slippage_bps=slippage_bps)
    risk["reference_net_rr"] = check["net_rr"]
    if not check["eligible"]:
        return finish("reference_rr_below_minimum" if check["reason"] == "entry_rr_below_minimum" else check["reason"])
    return finish("entry_plan_ready", True)


def build_stop_update(position, frame, *, source_as_of, observed_at, enabled_at):
    """Raise only for a new post-entry higher low supported by the daily MA band."""
    if position.get("risk_plan", {}).get("strategy_version") != POLICY_VERSION:
        raise ValueError("holding strategy version cannot change to v2")
    observed, enabled = _stamp(observed_at), _stamp(enabled_at)
    if observed < enabled:
        raise ValueError("structure observation precedes policy activation")
    if source_as_of < position["entry_date"]:
        return None
    used, fingerprint, observed = _daily(frame, source_as_of, observed_at, position["entry_date"])
    old_stop = _number(position["stop"], "current stop", positive=True)
    atr_value = _number(atr(used, 14).iloc[-1], "ATR", positive=True)
    ma20, ema20 = float(sma(used["Close"], 20).iloc[-1]), float(ema(used["Close"], 20).iloc[-1])
    band_low, close = min(ma20, ema20), float(used["Close"].iloc[-1])
    _, lows = swing_pivots(used, 3)
    pivot_low = pivot_date = previous_low = confirmed = candidate = None
    reason, new_stop = "no_new_confluent_higher_low", old_stop
    if len(lows):
        pivot = int(lows[-1])
        pivot_low, pivot_date = float(used["Low"].iloc[pivot]), used.index[pivot].date().isoformat()
        confirmed = _completed(used.index[pivot + 3].date()).isoformat()
        if len(lows) >= 2:
            previous_low = float(used["Low"].iloc[int(lows[-2])])
            if (pivot_date > position["entry_date"] and pivot_low > previous_low
                    and abs(pivot_low - band_low) <= atr_value and close >= max(pivot_low, band_low)):
                candidate = _buffered(min(pivot_low, band_low), atr_value, .1)
                if candidate > 0 and candidate < close and Decimal(str(candidate)) - Decimal(str(old_stop)) >= Decimal("0.01"):
                    reason, new_stop = "confluent_higher_low_confirmed", candidate
    identity = {"policy_version": POLICY_VERSION, "ticker": position["ticker"],
                "source_signal_id": position["source_signal_id"], "entry": position.get("entry_timestamp") or position["entry_date"],
                "source_as_of": source_as_of, "data_fingerprint": fingerprint}
    evidence = {"ma20": ma20, "ema20": ema20, "ma_band_low": band_low, "source_close": close,
                "atr": atr_value, "buffer_atr": .1, "buffer_value": atr_value * .1,
                "pivot_low": pivot_low, "pivot_date": pivot_date, "previous_pivot_low": previous_low,
                "pivot_confirmed_at": confirmed, "structure_candidate": candidate, "ma_candidate": None,
                "selected_basis": "confluent_higher_low" if new_stop != old_stop else None,
                "candidate_stop": candidate, "minimum_raise": .01, "data_as_of": source_as_of,
                "data_fingerprint": fingerprint, "source_snapshots": copy.deepcopy(frame.attrs.get("snapshots") or []),
                "policy_assumption": "项目假设：仅入场后右侧3日确认的更高低点，与20日均线带下沿1ATR内共振；取两者较低值减0.1ATR，只上移且低于确认收盘"}
    return {"update_id": hashlib.sha256(json.dumps(identity, sort_keys=True, allow_nan=False).encode()).hexdigest(),
            "ticker": position["ticker"], "source_signal_id": position["source_signal_id"], "new_stop": new_stop,
            "source_as_of": source_as_of, "source_completed_at": _completed(date.fromisoformat(source_as_of)).isoformat(),
            "observed_at": observed.isoformat(), "effective_at": max(observed, enabled).isoformat(),
            "policy_version": POLICY_VERSION, "reason": reason, "evidence": evidence}
