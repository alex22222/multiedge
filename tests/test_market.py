"""Yahoo snapshots keep separate provenance and never silently bypass errors."""
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, unquote, urlparse
from unittest.mock import patch

import pandas as pd
import pytest

from multiedge.market import fetch_yahoo_daily


def timestamp(value):
    return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp())


def payload(symbol="GOOGL"):
    return {"chart": {"error": None, "result": [{
        "meta": {"symbol": symbol, "exchangeTimezoneName": "America/New_York", "currency": "USD"},
        "timestamp": [timestamp("2025-01-02T14:30:00"), timestamp("2025-01-03T14:30:00")],
        "indicators": {"quote": [{"open": [100, 102], "high": [104, 106], "low": [99, 101],
                                     "close": [103, 105], "volume": [1000, 2000]}],
                       "adjclose": [{"adjclose": [1, 2]}]},
        "events": {"dividends": {"1": {"date": timestamp("2025-01-03T14:30:00"), "amount": .25}},
                   "splits": {"2": {"date": timestamp("2025-01-02T14:30:00"), "numerator": 4,
                                      "denominator": 1, "splitRatio": "4:1"}}},
    }]}}


def response(value):
    return io.BytesIO(json.dumps(value, separators=(",", ":")).encode())


def test_quotes_use_exchange_dates_request_cutoff_and_raw_close(tmp_path):
    value = payload()
    result = value["chart"]["result"][0]
    result["timestamp"][0] = timestamp("2025-01-03T00:30:00")  # New York is still Jan 2.
    result["timestamp"].append(timestamp("2025-01-06T14:30:00"))
    for key in ("open", "high", "low", "close", "volume"):
        result["indicators"]["quote"][0][key].append(999)
    with patch("urllib.request.urlopen", return_value=response(value)) as request:
        df = fetch_yahoo_daily("GOOGL", "2025-01-02", "2025-01-03", tmp_path)
    assert list(df.index.strftime("%Y-%m-%d")) == ["2025-01-02", "2025-01-03"]
    assert list(df.Close) == [103, 105]  # Adj Close is deliberately not substituted.
    assert df.attrs["out_of_range_rows"] == 1
    params = parse_qs(urlparse(request.call_args.args[0].full_url).query)
    assert params["period2"] == [str(timestamp("2025-01-04T00:00:00"))]
    assert params["events"] == ["div,splits"]


@pytest.mark.parametrize("ticker,mapped", [("SPX", "^GSPC"), ("BRK.B", "BRK-B")])
def test_provider_symbol_mapping_and_quoted_url(ticker, mapped, tmp_path):
    with patch("urllib.request.urlopen", return_value=response(payload(mapped))) as request:
        df = fetch_yahoo_daily(ticker, "2025-01-02", "2025-01-03", tmp_path)
    assert unquote(urlparse(request.call_args.args[0].full_url).path).endswith("/" + mapped)
    assert df.attrs["provider_symbol"] == mapped
    assert df.attrs["ticker"] == ticker


def test_exact_cache_verifies_raw_csv_metadata_and_avoids_network(tmp_path):
    raw = json.dumps(payload(), separators=(",", ":")).encode()
    with patch("urllib.request.urlopen", return_value=io.BytesIO(raw)):
        first = fetch_yahoo_daily("GOOGL", "2025-01-02", "2025-01-03", tmp_path)
    csv = tmp_path / "GOOGL_2025-01-02_2025-01-03.csv"
    meta_path = csv.with_suffix(".meta.json")
    meta = json.loads(meta_path.read_text())
    assert meta["raw_sha256"] == hashlib.sha256(raw).hexdigest()
    assert meta["csv_sha256"] == hashlib.sha256(csv.read_bytes()).hexdigest()
    assert (tmp_path / "raw" / f"{meta['raw_sha256']}.json").read_bytes() == raw
    assert meta["events"]["splits"][0]["date"] == "2025-01-02"
    assert meta["events"]["dividends"][0]["amount"] == .25
    assert meta["actual_end"] == "2025-01-03"
    assert "Adj Close unused" in meta["price_basis"]
    with patch("urllib.request.urlopen", side_effect=AssertionError("cache must avoid network")):
        second = fetch_yahoo_daily("GOOGL", "2025-01-02", "2025-01-03", tmp_path)
    pd.testing.assert_frame_equal(first, second)
    assert second.attrs == first.attrs


@pytest.mark.parametrize("part", ["csv", "raw", "metadata"])
def test_tampered_exact_cache_fails_without_silent_refetch(part, tmp_path):
    with patch("urllib.request.urlopen", return_value=response(payload())):
        fetch_yahoo_daily("GOOGL", "2025-01-02", "2025-01-03", tmp_path)
    csv = tmp_path / "GOOGL_2025-01-02_2025-01-03.csv"
    meta_path = csv.with_suffix(".meta.json")
    meta = json.loads(meta_path.read_text())
    if part == "metadata":
        meta["actual_end"] = "2025-01-02"
        meta_path.write_text(json.dumps(meta))
    else:
        path = csv if part == "csv" else tmp_path / "raw" / f"{meta['raw_sha256']}.json"
        path.write_bytes(path.read_bytes() + b"tampered")
    with patch("urllib.request.urlopen", side_effect=AssertionError("must not hide corruption")):
        with pytest.raises(ValueError, match="cache"):
            fetch_yahoo_daily("GOOGL", "2025-01-02", "2025-01-03", tmp_path)


def test_missing_rows_are_counted_but_all_missing_fails(tmp_path):
    value = payload()
    value["chart"]["result"][0]["indicators"]["quote"][0]["low"][0] = None
    with patch("urllib.request.urlopen", return_value=response(value)):
        df = fetch_yahoo_daily("GOOGL", "2025-01-02", "2025-01-03", tmp_path)
    assert len(df) == 1 and df.attrs["missing_rows"] == 1
    value["chart"]["result"][0]["indicators"]["quote"][0]["low"][1] = None
    with patch("urllib.request.urlopen", return_value=response(value)):
        with pytest.raises(ValueError, match="no valid"):
            fetch_yahoo_daily("GOOGL", "2025-01-01", "2025-01-03", tmp_path)


@pytest.mark.parametrize("change", ["symbol", "timezone", "array", "negative", "duplicate"])
def test_malformed_provider_data_fails_explicitly(change, tmp_path):
    value = payload()
    item = value["chart"]["result"][0]
    if change == "symbol":
        item["meta"]["symbol"] = "NVDA"
    elif change == "timezone":
        item["meta"]["exchangeTimezoneName"] = "UTC"
    elif change == "array":
        item["indicators"]["quote"][0]["high"].pop()
    elif change == "negative":
        item["indicators"]["quote"][0]["volume"][0] = -1
    else:
        item["timestamp"][1] = item["timestamp"][0]
    with patch("urllib.request.urlopen", return_value=response(value)):
        with pytest.raises(ValueError):
            fetch_yahoo_daily("GOOGL", "2025-01-02", "2025-01-03", tmp_path)
    assert not list(tmp_path.glob("*.csv"))


def test_provider_error_and_http_error_are_not_replaced_with_empty_data(tmp_path):
    value = {"chart": {"result": None, "error": {"code": "Not Found", "description": "No data"}}}
    with patch("urllib.request.urlopen", return_value=response(value)):
        with pytest.raises(RuntimeError, match="Not Found"):
            fetch_yahoo_daily("GOOGL", "2025-01-02", "2025-01-03", tmp_path)
    failure = HTTPError("https://query1.finance.yahoo.com", 429, "limited", {}, None)
    with patch("urllib.request.urlopen", side_effect=failure):
        with pytest.raises(HTTPError):
            fetch_yahoo_daily("GOOGL", "2025-01-02", "2025-01-03", tmp_path)


@pytest.mark.parametrize("ticker,start,end", [
    ("../GOOGL", "2025-01-02", "2025-01-03"),
    ("GOOGL", "2025-01-04", "2025-01-03"),
    ("GOOGL", "not-a-date", "2025-01-03"),
])
def test_invalid_request_never_contacts_provider(ticker, start, end, tmp_path):
    with patch("urllib.request.urlopen", side_effect=AssertionError("invalid request")):
        with pytest.raises(ValueError):
            fetch_yahoo_daily(ticker, start, end, tmp_path)
