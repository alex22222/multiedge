"""行情数据：MarketWatch 历史 CSV（Dow Jones 数据）下载 + 本地缓存。

注意：downloaddatapartial 端点单请求实际返回约 250 根日K（请求更早的 start
会被截断），文件名保留请求值，真实起点以文件内容为准——GOOGL/SPX 缓存为
2024-08-08 → 2025-08-08 共 251 个交易日。

数据源说明（可复现性）：日线 OHLCV，未复权口径（GOOGL 无分红，SPX 为指数）。
缓存文件命名 data/<symbol>_<start>_<end>.csv，命中即读，保证同一数据快照输出唯一。
"""
import os
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import pandas as pd

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# MarketWatch 路径前缀：个股 /investing/stock/，指数 /investing/index/
_INDEX_SYMBOLS = {"SPX", "SP500", "COMP", "DJIA"}


def mw_url(symbol: str, start: str, end: str) -> str:
    kind = "index" if symbol.upper().split(".")[0] in _INDEX_SYMBOLS else "stock"
    sym = symbol.lower()
    s = datetime.strptime(start, "%Y-%m-%d").strftime("%m/%d/%Y")
    e = datetime.strptime(end, "%Y-%m-%d").strftime("%m/%d/%Y")
    return (f"https://www.marketwatch.com/investing/{kind}/{sym}/downloaddatapartial"
            f"?startdate={s}%2000:00:00&enddate={e}%2000:00:00"
            f"&daterange=d30&frequency=p1d&csvdownload=true&downloadpartial=false&newdates=false")


def fetch_daily(symbol: str, start: str, end: str, cache_dir: str = "data",
                sleep: float = 2.0) -> pd.DataFrame:
    """下载并缓存日线。symbol 形如 GOOGL 或 SPX（标普500）。"""
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    safe = symbol.upper().replace("^", "")
    path = Path(cache_dir) / f"{safe}_{start}_{end}.csv"
    if not path.exists():
        cached = load_cached_daily(symbol, start, end, cache_dir)
        if cached is not None:
            return cached
        req = urllib.request.Request(mw_url(symbol, start, end), headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = resp.read().decode()
        if not payload.startswith("Date,"):
            raise RuntimeError(f"MarketWatch 返回非 CSV（可能被限流）: {payload[:120]}")
        path.write_text(payload, encoding="utf-8")
        time.sleep(sleep)
    return load_daily(path)


def fetch_daily_chunked(symbol: str, start: str, end: str, cache_dir: str = "data",
                        chunk_days: int = 360) -> pd.DataFrame:
    """分块获取长历史（MarketWatch 单请求约 250 根上限），按 chunk 缓存后合并。

    200MA 等长周期指标需要 ≥330 根数据才能覆盖图表窗口，故报告/长窗分析应使用本函数。
    """
    cached = load_cached_daily(symbol, start, end, cache_dir)
    if cached is not None:
        return cached
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    from datetime import timedelta
    s0 = datetime.strptime(start, "%Y-%m-%d")
    e0 = datetime.strptime(end, "%Y-%m-%d")
    parts = []
    cur = s0
    while cur <= e0:
        nxt = min(cur + timedelta(days=chunk_days), e0)
        a, b = cur.strftime("%Y-%m-%d"), nxt.strftime("%Y-%m-%d")
        safe = symbol.upper().replace("^", "")
        cp = Path(cache_dir) / f"{safe}_{a}_{b}.csv"
        if cp.exists():
            parts.append(load_daily(cp))
        else:
            req = urllib.request.Request(mw_url(symbol, a, b), headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = resp.read().decode()
            if not payload.startswith("Date,"):
                raise RuntimeError(f"MarketWatch 返回非 CSV: {payload[:120]}")
            cp.write_text(payload, encoding="utf-8")
            time.sleep(2.0)
            parts.append(load_daily(cp))
        cur = nxt + timedelta(days=1)
    out = pd.concat(parts).sort_index()
    out = out[~out.index.duplicated(keep="last")]
    safe = symbol.upper().replace("^", "")
    full = Path(cache_dir) / f"{safe}_{start}_{end}.csv"
    full.write_text(_to_csv(out), encoding="utf-8")
    return out


def load_cached_daily(symbol: str, start: str, end: str, cache_dir: str = "data"):
    """复用实际覆盖请求区间的缓存；返回截止日切片，不使用文件名推断真实起点。"""
    safe = symbol.upper().replace("^", "")
    begin, finish = pd.Timestamp(start), pd.Timestamp(end)
    # 周末请求对应上一个周五；交易所假日若无覆盖仍走原获取路径。
    expected_end = finish - pd.Timedelta(days=max(0, finish.weekday() - 4))
    candidates = []
    for path in sorted(Path(cache_dir).glob(f"{safe}_*.csv")):
        frame = load_daily(path)
        if frame.empty or frame.index[-1] < expected_end:
            continue
        if frame.index[0] > begin + pd.Timedelta(days=7):
            continue
        sliced = frame.loc[start:end].copy()
        if not sliced.empty:
            sliced.attrs["source_path"] = str(path)
            candidates.append(sliced)
    return max(candidates, key=len) if candidates else None


def _to_csv(df: pd.DataFrame) -> str:
    d = df.reset_index()
    if "Date" not in d.columns:
        d = d.rename(columns={d.columns[0]: "Date"})
    d["Date"] = pd.to_datetime(d["Date"]).dt.strftime("%Y-%m-%d")
    cols = [c for c in ("Date", "Open", "High", "Low", "Close", "Volume") if c in d.columns]
    return d[cols].to_csv(index=False)


def load_daily(path) -> pd.DataFrame:
    df = pd.read_csv(path, thousands=",", parse_dates=["Date"], index_col="Date")
    df = df.sort_index()
    for c in ("Open", "High", "Low", "Close"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    if "Volume" in df.columns:
        df["Volume"] = pd.to_numeric(df["Volume"], errors="coerce")
    return df.dropna(subset=["Close"])
