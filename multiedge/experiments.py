"""Versionable personal indicator filters, independent of the frozen engine.

RSI uses Wilder's arithmetic seed and recursive smoothing. MACD uses pandas
adjust=False EMAs seeded by the first close, with explicit warm-up periods.
The caller must pass only bars available at the requested analysis time.
"""
from dataclasses import asdict, dataclass
import math

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ExperimentConfig:
    name: str = "个人指标实验"
    mode: str = "baseline_filter"
    rsi_enabled: bool = False
    rsi_period: int = 14
    rsi_min: float = 40
    rsi_max: float = 70
    macd_enabled: bool = False
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9

    def validate(self):
        """Validate untrusted HTTP/JSON configuration before evaluation."""
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 100:
            raise ValueError("name must contain 1–100 characters")
        if self.mode not in ("baseline_filter", "custom"):
            raise ValueError("mode must be baseline_filter or custom")
        for key in ("rsi_enabled", "macd_enabled"):
            if not isinstance(getattr(self, key), bool):
                raise ValueError(f"{key} must be a boolean")
        for key in ("rsi_period", "macd_fast", "macd_slow", "macd_signal"):
            value = getattr(self, key)
            if type(value) is not int or not 1 <= value <= 500:
                raise ValueError(f"{key} must be an integer between 1 and 500")
        if self.rsi_period < 2 or self.macd_fast >= self.macd_slow:
            raise ValueError("rsi_period must be ≥2; macd_fast must be < macd_slow")
        for key in ("rsi_min", "rsi_max"):
            value = getattr(self, key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{key} must be finite")
        if not 0 <= self.rsi_min <= self.rsi_max <= 100:
            raise ValueError("RSI range must satisfy 0 ≤ min ≤ max ≤ 100")
        if self.mode == "custom" and not (self.rsi_enabled or self.macd_enabled):
            raise ValueError("custom mode requires at least one enabled indicator")
        return self


def rsi_wilder(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI after period consecutive changes; missing prices restart warm-up."""
    changes = pd.to_numeric(close, errors="coerce").diff().to_numpy(dtype=float)
    values = np.full(len(changes), np.nan)
    count, gains, losses = 0, 0.0, 0.0
    avg_gain = avg_loss = None
    for i in range(1, len(changes)):
        change = changes[i]
        if not np.isfinite(change):
            count, gains, losses = 0, 0.0, 0.0
            avg_gain = avg_loss = None
            continue
        gain, loss = max(change, 0), max(-change, 0)
        if avg_gain is None:
            count += 1
            gains += gain
            losses += loss
            if count < period:
                continue
            avg_gain, avg_loss = gains / period, losses / period
        else:
            avg_gain = (avg_gain * (period - 1) + gain) / period
            avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_gain == 0 and avg_loss == 0:
            values[i] = 50.0
        elif avg_loss == 0:
            values[i] = 100.0
        else:
            values[i] = 100 - 100 / (1 + avg_gain / avg_loss)
    return pd.Series(values, index=close.index, name="rsi")


def _last(series):
    if series.empty or not np.isfinite(series.iloc[-1]):
        return None
    return float(series.iloc[-1])


def evaluate_experiment(df, baseline_verdict, config: ExperimentConfig) -> dict:
    """Apply explicitly selected filters without changing baseline results.

The MACD filter is a state test (MACD > signal), not a one-day crossover.
Warm-up or missing indicator values fail an enabled filter closed.
"""
    config.validate()
    if baseline_verdict not in ("BUY", "WATCH", "NO"):
        raise ValueError("baseline_verdict must be BUY, WATCH or NO")
    close = pd.to_numeric(df["Close"], errors="coerce") if "Close" in df else pd.Series(dtype=float)
    close = close.where(close > 0)
    rsi = rsi_wilder(close, config.rsi_period)
    missing = np.flatnonzero(close.isna().to_numpy())
    continuous = close.iloc[missing[-1] + 1:] if len(missing) else close
    fast = continuous.ewm(span=config.macd_fast, adjust=False, min_periods=config.macd_fast).mean()
    slow = continuous.ewm(span=config.macd_slow, adjust=False, min_periods=config.macd_slow).mean()
    macd = fast - slow
    signal = macd.ewm(span=config.macd_signal, adjust=False, min_periods=config.macd_signal).mean()
    values = {"rsi": _last(rsi), "macd": _last(macd), "macd_signal": _last(signal),
              "macd_histogram": _last(macd - signal)}
    # pandas EMA can carry values across a missing close; do not call that a new observation.
    if close.empty or pd.isna(close.iloc[-1]):
        values = {key: None for key in values}
    checks = []
    if config.rsi_enabled:
        value = values["rsi"]
        checks.append({"key": "rsi", "label": "RSI 区间", "enabled": True,
                       "ok": value is not None and config.rsi_min <= value <= config.rsi_max,
                       "value": value, "threshold": f"{config.rsi_min} ≤ RSI ≤ {config.rsi_max}",
                       "reason": "数据不足或缺失" if value is None else "Wilder RSI"})
    if config.macd_enabled:
        value, threshold = values["macd"], values["macd_signal"]
        checks.append({"key": "macd", "label": "MACD 高于信号线", "enabled": True,
                       "ok": value is not None and threshold is not None and value > threshold,
                       "value": value, "threshold": "MACD > signal（状态，非当日金叉）",
                       "reason": "数据不足或缺失" if threshold is None else "EMA MACD"})
    passed = all(check["ok"] for check in checks)
    eligible = passed and (config.mode == "custom" or baseline_verdict == "BUY")
    if config.mode == "custom":
        verdict = "BUY" if eligible else "NO"
    else:
        verdict = "WATCH" if baseline_verdict == "BUY" and not passed else baseline_verdict
    return {"baseline_verdict": baseline_verdict, "verdict": verdict, "eligible": bool(eligible),
            "values": values, "checks": checks, "config": asdict(config)}
