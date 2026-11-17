"""Personal indicator rules remain separate from the frozen strategy."""
import dataclasses

import numpy as np
import pandas as pd
import pytest

from multiedge.experiments import ExperimentConfig, evaluate_experiment, rsi_wilder


def frame(values):
    return pd.DataFrame({"Close": values}, index=pd.bdate_range("2025-01-01", periods=len(values)))


def test_disabled_filters_preserve_each_baseline_verdict():
    df = frame(range(100, 150))
    for verdict in ("BUY", "WATCH", "NO"):
        result = evaluate_experiment(df, verdict, ExperimentConfig())
        assert result["baseline_verdict"] == result["verdict"] == verdict
        assert result["eligible"] == (verdict == "BUY")


def test_rsi_uses_wilder_seed_and_handles_flat_or_one_way_prices():
    values = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42,
              45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28]
    rsi = rsi_wilder(pd.Series(values), 14)
    assert rsi.iloc[:14].isna().all()
    assert rsi.iloc[14] == pytest.approx(70.4641350211)
    for close, expected in ((range(30), 100), (range(30, 0, -1), 0), ([100] * 30, 50)):
        assert rsi_wilder(pd.Series(close, dtype=float), 14).iloc[-1] == expected


def test_enabled_rsi_filters_buy_without_changing_original_result():
    result = evaluate_experiment(frame(range(100, 150)), "BUY", ExperimentConfig(rsi_enabled=True))
    assert result["baseline_verdict"] == "BUY"
    assert result["verdict"] == "WATCH" and not result["eligible"]
    assert result["values"]["rsi"] == 100
    assert result["checks"][0]["ok"] is False


def test_custom_mode_requires_rules_and_can_qualify_independently():
    with pytest.raises(ValueError, match="custom"):
        ExperimentConfig(mode="custom").validate()
    cfg = ExperimentConfig(mode="custom", rsi_enabled=True, rsi_min=90, rsi_max=100)
    result = evaluate_experiment(frame(range(100, 150)), "NO", cfg)
    assert result["verdict"] == "BUY" and result["baseline_verdict"] == "NO"
    assert result["config"]["mode"] == "custom"


def test_enabled_filters_fail_closed_during_warmup():
    cfg = ExperimentConfig(rsi_enabled=True, macd_enabled=True)
    result = evaluate_experiment(frame([100, 101, 102]), "BUY", cfg)
    assert not result["eligible"]
    assert all(value is None for value in result["values"].values())
    assert all(not check["ok"] for check in result["checks"])


def test_macd_has_explicit_signal_warmup_and_matches_ema_definition():
    df = frame(100 + np.arange(60) ** 1.2)
    cfg = ExperimentConfig(macd_enabled=True)
    result = evaluate_experiment(df, "BUY", cfg)
    fast = df.Close.ewm(span=12, adjust=False, min_periods=12).mean()
    slow = df.Close.ewm(span=26, adjust=False, min_periods=26).mean()
    expected = fast - slow
    signal = expected.ewm(span=9, adjust=False, min_periods=9).mean()
    assert result["values"]["macd"] == pytest.approx(expected.iloc[-1])
    assert result["values"]["macd_signal"] == pytest.approx(signal.iloc[-1])
    assert result["values"]["macd_histogram"] == pytest.approx((expected - signal).iloc[-1])
    short = evaluate_experiment(df.iloc[:30], "BUY", cfg)
    assert short["values"]["macd"] is not None
    assert short["values"]["macd_signal"] is None and not short["eligible"]


def test_indicator_prefix_does_not_depend_on_future_prices():
    df = frame(np.linspace(100, 140, 50))
    prefix = df.iloc[:40]
    cfg = ExperimentConfig(rsi_enabled=True, macd_enabled=True)
    first = evaluate_experiment(prefix, "BUY", cfg)
    df.iloc[40:, 0] = 1_000_000
    assert evaluate_experiment(df.iloc[:40], "BUY", cfg) == first


def test_missing_close_requires_macd_warmup_again():
    df = frame(np.linspace(100, 140, 60))
    df.iloc[-10, 0] = np.nan
    result = evaluate_experiment(df, "BUY", ExperimentConfig(macd_enabled=True))
    assert not result["eligible"] and result["values"]["macd_signal"] is None


@pytest.mark.parametrize("changes", [
    {"rsi_period": True}, {"rsi_period": 0}, {"rsi_min": 80, "rsi_max": 20},
    {"rsi_max": float("nan")}, {"rsi_enabled": "false"}, {"macd_fast": 30},
    {"macd_signal": 0}, {"mode": "live"}, {"name": ""},
])
def test_configuration_rejects_invalid_boundary_values(changes):
    with pytest.raises(ValueError):
        dataclasses.replace(ExperimentConfig(), **changes).validate()
