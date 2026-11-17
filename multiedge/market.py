"""Yahoo chart snapshots in an explicitly separate source directory.

The caller must pass a dedicated directory (for example data/yahoo), never the
original MarketWatch cache. OHLC Close is used as provided; Adj Close is not
substituted. Source JSON and corporate events are retained for audit. The caller
chooses a completed-session end date; this module does not infer market holidays
or claim that a returned earlier last date is current.
"""
from datetime import date, datetime, time, timedelta, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

import pandas as pd


PROVIDER = "Yahoo Finance chart"
PRICE_BASIS = "Yahoo chart OHLC Close; Adj Close unused"
NY = ZoneInfo("America/New_York")
SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,11}$")
HASH = re.compile(r"^[0-9a-f]{64}$")


def _sha(payload):
    return hashlib.sha256(payload).hexdigest()


def _json(payload):
    def invalid_constant(value):
        raise ValueError(f"non-finite JSON number: {value}")
    return json.loads(payload, parse_constant=invalid_constant)


def _finite(value, field, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value) or (positive and value <= 0):
        raise ValueError(f"Yahoo invalid {field}")
    return float(value)


def _session(timestamp):
    if type(timestamp) is not int:
        raise ValueError("Yahoo timestamp must be an integer")
    try:
        return datetime.fromtimestamp(timestamp, timezone.utc).astimezone(NY).date().isoformat()
    except (ValueError, OverflowError, OSError) as error:
        raise ValueError("Yahoo timestamp is outside the supported range") from error


def _events(raw, start, end):
    if not isinstance(raw, dict):
        raise ValueError("Yahoo events must be an object")
    result = {"dividends": [], "splits": []}
    for kind in result:
        entries = raw.get(kind, {})
        if not isinstance(entries, dict):
            raise ValueError(f"Yahoo {kind} must be an object")
        for entry in entries.values():
            if not isinstance(entry, dict) or "date" not in entry:
                raise ValueError(f"Yahoo invalid {kind} event")
            day = _session(entry["date"])
            if not start <= day <= end:
                continue
            item = {"date": day, "timestamp": entry["date"]}
            if kind == "dividends":
                amount = _finite(entry.get("amount"), "dividend amount")
                if amount < 0:
                    raise ValueError("Yahoo dividend amount must be non-negative")
                item["amount"] = amount
            else:
                numerator = _finite(entry.get("numerator"), "split numerator", positive=True)
                denominator = _finite(entry.get("denominator"), "split denominator", positive=True)
                item.update({"numerator": numerator, "denominator": denominator,
                             "split_ratio": entry.get("splitRatio", f"{numerator}:{denominator}")})
                if not isinstance(item["split_ratio"], str):
                    raise ValueError("Yahoo split ratio must be text")
            result[kind].append(item)
        result[kind].sort(key=lambda item: (item["date"], item["timestamp"]))
    return result


def _parse(payload, provider_symbol, start, end):
    try:
        value = _json(payload)
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError("Yahoo response is not valid JSON") from error
    chart = value.get("chart") if isinstance(value, dict) else None
    if not isinstance(chart, dict):
        raise ValueError("Yahoo response is missing chart")
    if chart.get("error") is not None:
        raise RuntimeError(f"Yahoo chart error: {chart['error']}")
    results = chart.get("result")
    if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict):
        raise ValueError("Yahoo response must contain one result")
    item = results[0]
    meta = item.get("meta")
    if not isinstance(meta, dict) or meta.get("symbol") != provider_symbol:
        raise ValueError("Yahoo response symbol does not match request")
    if meta.get("exchangeTimezoneName") not in ("America/New_York", "US/Eastern"):
        raise ValueError("Yahoo response does not identify the New York exchange timezone")
    currency = meta.get("currency")
    if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("Yahoo response is missing a valid currency")
    stamps = item.get("timestamp")
    indicators = item.get("indicators")
    quotes = indicators.get("quote") if isinstance(indicators, dict) else None
    if (not isinstance(stamps, list) or not isinstance(quotes, list)
            or len(quotes) != 1 or not isinstance(quotes[0], dict)):
        raise ValueError("Yahoo response is missing timestamp/OHLCV arrays")
    quote = quotes[0]
    columns = ("open", "high", "low", "close", "volume")
    if any(not isinstance(quote.get(key), list) or len(quote[key]) != len(stamps) for key in columns):
        raise ValueError("Yahoo OHLCV arrays do not align with timestamps")
    rows, seen = [], set()
    missing, outside = 0, 0
    for i, stamp in enumerate(stamps):
        day = _session(stamp)
        if not start <= day <= end:
            outside += 1
            continue
        if day in seen:
            raise ValueError("Yahoo response contains duplicate session dates")
        seen.add(day)
        values = [quote[key][i] for key in columns]
        if any(value is None for value in values):
            missing += 1
            continue
        o, h, low, close = [_finite(value, field, positive=True) for value, field in zip(values[:4], columns[:4])]
        volume = _finite(values[4], "volume")
        if volume < 0 or not volume.is_integer() or h < max(o, close) or low > min(o, close):
            raise ValueError("Yahoo invalid OHLCV price range or volume")
        rows.append({"Date": day, "Open": o, "High": h, "Low": low, "Close": close, "Volume": int(volume)})
    if not rows:
        raise ValueError("Yahoo returned no valid OHLCV rows in requested range")
    frame = pd.DataFrame(rows).set_index("Date").sort_index()
    frame.index = pd.DatetimeIndex(frame.index, name="Date").astype("datetime64[ns]")
    details = {"exchange_timezone": "America/New_York", "currency": currency,
               "actual_start": str(frame.index[0].date()), "actual_end": str(frame.index[-1].date()),
               "rows": len(frame), "raw_rows": len(stamps), "missing_rows": missing,
               "out_of_range_rows": outside, "events": _events(item.get("events", {}), start, end)}
    return frame, details


def _csv(frame):
    return frame.to_csv(index_label="Date", date_format="%Y-%m-%d").encode("utf-8")


def _atomic(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".snapshot-", delete=False) as output:
            temporary = Path(output.name)
            output.write(payload)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _read_cache(csv_path, meta_path, cache_dir, ticker, provider_symbol, start, end):
    try:
        meta = _json(meta_path.read_bytes())
        expected = {"schema_version": 1, "provider": PROVIDER, "price_basis": PRICE_BASIS,
                    "ticker": ticker, "provider_symbol": provider_symbol, "request_start": start, "request_end": end}
        if not isinstance(meta, dict) or any(meta.get(key) != value for key, value in expected.items()):
            raise ValueError("source/request metadata mismatch")
        if not all(isinstance(meta.get(key), str) and HASH.fullmatch(meta[key]) for key in ("raw_sha256", "csv_sha256")):
            raise ValueError("missing content hashes")
        retrieved = datetime.fromisoformat(meta["retrieved_at"])
        if retrieved.tzinfo is None or retrieved.utcoffset() is None:
            raise ValueError("retrieval timestamp must have a timezone")
        raw = (cache_dir / "raw" / f"{meta['raw_sha256']}.json").read_bytes()
        if _sha(raw) != meta["raw_sha256"] or _sha(csv_path.read_bytes()) != meta["csv_sha256"]:
            raise ValueError("content hash mismatch")
        frame, details = _parse(raw, provider_symbol, start, end)
        if _sha(_csv(frame)) != meta["csv_sha256"] or any(meta.get(key) != value for key, value in details.items()):
            raise ValueError("CSV/range/events do not match immutable source")
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        raise ValueError(f"Yahoo cache verification failed: {error}") from error
    frame.attrs = {**meta, "source_path": str(csv_path)}
    return frame


def fetch_yahoo_daily(ticker, start, end, cache_dir) -> pd.DataFrame:
    """Fetch or verify one exact request in a dedicated Yahoo cache directory.

Null rows are excluded with counts in attrs; malformed source data fails. Exact
cached snapshots are immutable and hash-verified; corruption is surfaced rather
than silently refetched. Exchange dates, not UTC timestamp dates, bound bars.
"""
    if not isinstance(ticker, str):
        raise ValueError("ticker must be text")
    ticker = ticker.strip().upper()
    if not SYMBOL.fullmatch(ticker):
        raise ValueError("invalid ticker")
    try:
        begin, finish = date.fromisoformat(start), date.fromisoformat(end)
    except (TypeError, ValueError) as error:
        raise ValueError("start/end must be ISO dates") from error
    if begin > finish:
        raise ValueError("start must not exceed end")
    start, end = begin.isoformat(), finish.isoformat()
    provider_symbol = "^GSPC" if ticker == "SPX" else ticker.replace(".", "-")
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    csv_path = cache_dir / f"{ticker}_{start}_{end}.csv"
    meta_path = csv_path.with_suffix(".meta.json")
    with csv_path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if csv_path.exists() or meta_path.exists():
            if not (csv_path.exists() and meta_path.exists()):
                raise ValueError("Yahoo cache is incomplete (CSV and metadata are both required)")
            return _read_cache(csv_path, meta_path, cache_dir, ticker, provider_symbol, start, end)
        period1 = int(datetime.combine(begin, time(), timezone.utc).timestamp())
        period2 = int(datetime.combine(finish + timedelta(days=1), time(), timezone.utc).timestamp())
        query = urllib.parse.urlencode({"period1": period1, "period2": period2, "interval": "1d", "events": "div,splits"})
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(provider_symbol, safe='')}?{query}"
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
        frame, details = _parse(raw, provider_symbol, start, end)
        csv_bytes = _csv(frame)
        raw_hash = _sha(raw)
        raw_path = cache_dir / "raw" / f"{raw_hash}.json"
        if raw_path.exists():
            if _sha(raw_path.read_bytes()) != raw_hash:
                raise ValueError("Yahoo immutable raw cache content hash mismatch")
        else:
            _atomic(raw_path, raw)
        meta = {"schema_version": 1, "provider": PROVIDER, "price_basis": PRICE_BASIS,
                "ticker": ticker, "provider_symbol": provider_symbol, "request_start": start, "request_end": end,
                "retrieved_at": datetime.now(timezone.utc).isoformat(), "raw_sha256": raw_hash,
                "csv_sha256": _sha(csv_bytes), "raw_artifact": f"raw/{raw_hash}.json", **details}
        _atomic(csv_path, csv_bytes)
        _atomic(meta_path, json.dumps(meta, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8"))
        frame.attrs = {**meta, "source_path": str(csv_path)}
        return frame
