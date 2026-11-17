"""指标单元测试。"""
import numpy as np
import pandas as pd
import pytest

from multiedge.indicators import sma, ema, atr, swing_pivots, rs_ratio, momentum_peak


def _df():
    idx = pd.date_range("2025-01-01", periods=60, freq="B")
    close = 100 + np.cumsum(np.random.default_rng(7).normal(0, 1, 60))
    high = close + 1.0
    low = close - 1.0
    open_ = close
    return pd.DataFrame({"Open": open_, "High": high, "Low": low,
                         "Close": close, "Volume": 1e6}, index=idx)


def test_atr_positive_and_stable():
    df = _df()
    a = atr(df, 14)
    assert (a.dropna() > 0).all()
    assert abs(a.iloc[-1] - a.iloc[-5]) < 5


def test_sma_ema_values():
    s = pd.Series([1.0] * 10 + [11.0] * 10)
    assert sma(s, 10).iloc[-1] == 11.0
    assert ema(s, 10).iloc[-1] > 1.0


def test_swing_pivots_detect_obvious_high():
    df = _df()
    df.loc[df.index[30], "High"] = df["High"].max() + 5   # 人为最高峰
    ph, pl = swing_pivots(df, 3)
    assert 30 in ph


def test_momentum_peak_unified():
    # 全程走平、仅第 10 日冲高：21 日窗收益只看端点（均为 0），峰取第一个终点（确定性）
    close = pd.Series([100.0] * 30)
    close.iloc[10] = 120.0
    t, r = momentum_peak(close, 29, window=21, lookback=42)
    assert t == 21 and r == 0.0

    # 末端上涨：最佳窗终点应贴近 T
    close2 = pd.Series([100.0] * 20 + [100.0 + i for i in range(1, 11)])
    t2, r2 = momentum_peak(close2, 29, window=21, lookback=42)
    assert r2 > 0 and t2 == 29


def test_rs_ratio():
    a = pd.Series([200.0, 202.0, 204.0])
    b = pd.Series([6000.0, 6060.0, 6120.0])
    r = rs_ratio(a, b)
    assert np.allclose(r.values, [200 / 6000, 202 / 6060, 204 / 6120])
