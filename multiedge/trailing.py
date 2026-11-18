"""Future-only daily MA/confirmed-higher-low proposals for Paper holdings.

The video supplies the structure and MA-with-buffer principles. The candidate
combination, confirmation lag, cent rounding and activation semantics here are
explicit project assumptions, not a verbatim video trading algorithm.
"""
import copy
from datetime import date, datetime, time, timezone
from decimal import Decimal, ROUND_FLOOR
import hashlib
import json
import math
from numbers import Real
from zoneinfo import ZoneInfo

import pandas as pd

from .config import Config
from .indicators import atr, ema, sma, swing_pivots


POLICY_VERSION = "structure_ma20_hl_v1"
POLICY = {"policy_version": POLICY_VERSION,
          "description": "完整日线20MA区间与已确认更高低点，只上移保护位，下一交易时段执行",
          "timeframe": "1d", "ma_period": 20, "atr_period": Config().atr_period,
          "pivot_k": Config().pivot_k, "buffer_atr": Config().stop_buffer_atr,
          "selection": "highest_confirmed_support", "rounding": "floor_cent",
          "minimum_raise": .01,
          "viewpoints": ["E1 [44:53–45:32]", "F2b [50:53–51:26]", "F4 [52:02–52:22]", "G1/G2"]}
NY = ZoneInfo("America/New_York")
UTC = timezone.utc
COLUMNS = ("Open", "High", "Low", "Close", "Volume")


def _stamp(value):
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError) as error:
        raise ValueError("trailing timestamps must be timezone-aware ISO values") from error
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("trailing timestamps must have a timezone")
    return result.astimezone(UTC)


def _completed(day):
    return datetime.combine(day, time(16, 30), NY).astimezone(UTC)


def _number(value, name, positive=False):
    if (isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value)
            or (positive and value <= 0)):
        raise ValueError(f"{name} must be a finite {'positive ' if positive else ''}number")
    return float(value)


def _buffered(level, atr_value, buffer_atr):
    value = Decimal(str(level)) - Decimal(str(atr_value)) * Decimal(str(buffer_atr))
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_FLOOR))


def build_stop_update(position, frame, *, source_as_of, observed_at, enabled_at):
    """Return one immutable daily proposal, or None for a pre-entry source day.

The caller deduplicates actual observations by holding, source day and policy.
This function performs no I/O and does not mutate the holding or its history.
An already-breached candidate still describes future protection: the executor
must compare a future eligible bar's actual opening price, never backfill an
exit at the source close or at an unavailable past stop price.
"""
    source_day = date.fromisoformat(source_as_of)
    entry_day = date.fromisoformat(position["entry_date"])
    observed, enabled = _stamp(observed_at), _stamp(enabled_at)
    source_completed = _completed(source_day)
    if observed < source_completed:
        raise ValueError("trailing source session is not complete at observation")
    if observed < enabled:
        raise ValueError("trailing observation precedes policy activation")
    if source_day < entry_day:
        return None
    old_stop = _number(position["stop"], "current stop", positive=True)
    if not position.get("source_signal_id") or not position.get("ticker"):
        raise ValueError("holding identity is required for a trailing proposal")
    if not isinstance(frame, pd.DataFrame) or not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError("trailing data must use a daily DatetimeIndex")
    if not all(column in frame for column in COLUMNS):
        raise ValueError("trailing source is missing OHLCV columns")
    # Truncate before any price validation or indicators, including pivots.
    try:
        used = frame.loc[:source_as_of, list(COLUMNS)].copy()
    except (KeyError, TypeError) as error:
        raise ValueError("trailing source dates cannot be sliced") from error
    if (len(used) < 21 or used.index[-1].date() != source_day
            or not used.index.is_monotonic_increasing or not used.index.normalize().is_unique):
        raise ValueError("trailing source must include the requested day and at least 21 unique daily bars")
    for row in used.itertuples(index=False, name=None):
        o, high, low, close = [_number(value, "OHLC", positive=True) for value in row[:4]]
        volume = _number(row[4], "volume")
        if volume < 0 or high < max(o, close) or low > min(o, close):
            raise ValueError("trailing source contains invalid OHLCV")
    # A later appended row can promote integer columns to float; equal used
    # values must retain the same source fingerprint regardless of that dtype.
    used = used.astype(float)
    events = frame.attrs.get("events") or {}
    if not isinstance(events, dict):
        raise ValueError("invalid split metadata in trailing source")
    splits = frame.attrs.get("splits", events.get("splits", []))
    if not isinstance(splits, list):
        raise ValueError("invalid split metadata in trailing source")
    for event in splits:
        try:
            split_day = date.fromisoformat(event["date"])
        except (TypeError, ValueError, KeyError) as error:
            raise ValueError("invalid split metadata in trailing source") from error
        if entry_day < split_day <= source_day:
            raise ValueError("split during holding requires corporate-action accounting before trailing")

    ma_values, ema_values = sma(used["Close"], 20), ema(used["Close"], 20)
    ma20 = _number(ma_values.iloc[-1], "MA20", positive=True)
    ema20 = _number(ema_values.iloc[-1], "EMA20", positive=True)
    ma_band_low = min(ma20, ema20)
    previous_band_low = min(_number(ma_values.iloc[-2], "previous MA20", positive=True),
                            _number(ema_values.iloc[-2], "previous EMA20", positive=True))
    cfg = Config()
    atr_value = _number(atr(used, cfg.atr_period).iloc[-1], "ATR", positive=True)
    close = float(used["Close"].iloc[-1])
    ma_candidate = _buffered(ma_band_low, atr_value, cfg.stop_buffer_atr)
    ma_rising = ma_band_low > previous_band_low
    ma_breached = close <= ma_candidate
    candidates = []
    if ma_candidate > 0 and (ma_breached or (ma_rising and close >= ma_band_low)):
        candidates.append((ma_candidate, "ma20", "ma20_breached_next_session" if ma_breached else "ma20_rising_support"))

    _, lows = swing_pivots(used, cfg.pivot_k)
    pivot_low = pivot_date = pivot_confirmed_at = previous_pivot_low = None
    structure_candidate = None
    if len(lows):
        pivot = int(lows[-1])
        pivot_low = float(used["Low"].iloc[pivot])
        pivot_day = used.index[pivot].date()
        pivot_date = pivot_day.isoformat()
        pivot_confirmed_at = _completed(used.index[pivot + cfg.pivot_k].date()).isoformat()
        if len(lows) >= 2:
            previous_pivot_low = float(used["Low"].iloc[int(lows[-2])])
            if pivot_day > entry_day and pivot_low > previous_pivot_low:
                value = _buffered(pivot_low, atr_value, cfg.stop_buffer_atr)
                if value > 0:
                    structure_candidate = value
                    reason = "higher_low_breached_next_session" if close <= value else "higher_low_confirmed"
                    candidates.append((value, "higher_low", reason))

    selected = max(candidates, key=lambda item: item[0]) if candidates else None
    enough_increase = selected and Decimal(str(selected[0])) - Decimal(str(old_stop)) >= Decimal("0.01")
    new_stop = selected[0] if enough_increase else old_stop
    reason = selected[2] if enough_increase else "no_higher_confirmed_support"
    fingerprint = hashlib.sha256(used.to_csv(index_label="Date", date_format="%Y-%m-%d").encode()).hexdigest()
    identity = {"policy_version": POLICY_VERSION, "ticker": position["ticker"],
                "source_signal_id": position["source_signal_id"],
                "entry": position.get("entry_timestamp") or position["entry_date"],
                "source_as_of": source_as_of, "data_fingerprint": fingerprint}
    update_id = hashlib.sha256(json.dumps(identity, sort_keys=True, allow_nan=False).encode()).hexdigest()
    evidence = {"ma20": ma20, "ema20": ema20, "ma_band_low": ma_band_low,
                "previous_ma_band_low": previous_band_low, "source_close": close,
                "atr": atr_value, "buffer_atr": cfg.stop_buffer_atr,
                "buffer_value": atr_value * cfg.stop_buffer_atr,
                "pivot_low": pivot_low, "pivot_date": pivot_date, "pivot_confirmed_at": pivot_confirmed_at,
                "previous_pivot_low": previous_pivot_low, "ma_candidate": ma_candidate,
                "structure_candidate": structure_candidate, "selected_basis": selected[1] if selected else None,
                "ma_band_rising": ma_rising, "ma_breached": ma_breached,
                "candidate_stop": selected[0] if selected else None, "minimum_raise": .01,
                "data_as_of": source_as_of, "data_fingerprint": fingerprint,
                "source_snapshots": copy.deepcopy(frame.attrs.get("snapshots") or []),
                "policy_assumption": "项目操作化：20SMA/20EMA带下沿与右侧3根完整日线确认的更高低点分别减0.1×ATR；"
                                     "取较高有效候选并向下取整到美分，至少上调0.01，真实登记后下一交易时段生效。"}
    return {"update_id": update_id, "ticker": position["ticker"], "source_signal_id": position["source_signal_id"],
            "new_stop": new_stop, "source_as_of": source_as_of,
            "source_completed_at": source_completed.isoformat(), "observed_at": observed.isoformat(),
            "effective_at": max(observed, enabled).isoformat(), "policy_version": POLICY_VERSION,
            "reason": reason, "evidence": evidence}
