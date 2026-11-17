"""技术指标：MA/EMA/ATR、分形 pivot、30周SMA、统一动能峰、RS 线。"""
import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Wilder ATR。"""
    h, l, c = df["High"], df["Low"], df["Close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def swing_pivots(df: pd.DataFrame, k: int = 3):
    """分形 pivot：高/低点严格大于/小于左右各 k 根K线。

    返回 (piv_idx_high, piv_idx_low) —— 位置索引数组（int）。
    """
    h, l = df["High"].values, df["Low"].values
    n = len(df)
    ph, pl = [], []
    for i in range(k, n - k):
        if h[i] == max(h[i - k:i + k + 1]) and (h[i] > h[i - k:i + k + 1]).sum() >= 2 * k - 1:
            ph.append(i)
        if l[i] == min(l[i - k:i + k + 1]) and (l[i] < l[i - k:i + k + 1]).sum() >= 2 * k - 1:
            pl.append(i)
    return np.array(ph), np.array(pl)



def rs_ratio(stock_close: pd.Series, spx_close: pd.Series) -> pd.Series:
    """RS 线 = 个股/指数 价格比。"""
    return stock_close / spx_close


def weekly_sma(close: pd.Series, weeks: int = 30) -> pd.Series:
    """周级 SMA：按周五重采样收盘，rolling(weeks) 后前向填充到日频。"""
    w = close.resample("W-FRI").last().dropna()
    return w.rolling(weeks).mean().reindex(close.index).ffill()


def momentum_peak(close, T: int, window: int = 21, lookback: int = 42):
    """统一"动能峰"定义：近 lookback 日内最佳 window 日对数收益窗的终点索引。

    engine（首回调龄/催化剂窗/量能 climax）与 momentum_edge 共用同一峰，避免两处不一致。
    返回 (peak_idx, best_log_return)。"""
    c = close.values if hasattr(close, "values") else close
    best_r, best_t = -9.9, None
    lo = max(window, T - lookback + window)
    for t in range(lo, T + 1):
        r = np.log(c[t] / c[t - window])
        if r > best_r:
            best_r, best_t = r, t
    return best_t, best_r
