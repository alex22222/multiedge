"""Completed daily structure can propose future-only, monotonic stop updates."""
from datetime import datetime

import pandas as pd
import pytest

from multiedge.trailing import POLICY_VERSION, build_stop_update


def prices():
    index = pd.bdate_range(end="2026-11-17", periods=60)
    close = pd.Series([90 + .5 * i for i in range(60)], index=index)
    return pd.DataFrame({"Open": close - .1, "High": close + .5,
                         "Low": close - .5, "Close": close, "Volume": 1000})


def position(**values):
    return {"ticker": "ABC", "source_signal_id": "signal-1", "entry_date": "2026-11-16",
            "entry_timestamp": "2026-11-16T14:30:00+00:00", "stop": 100, **values}


def build(frame=None, holding=None, **kwargs):
    kwargs.setdefault("source_as_of", "2026-11-17")
    kwargs.setdefault("observed_at", "2026-11-17T23:30:00+00:00")
    kwargs.setdefault("enabled_at", "2026-11-01T00:00:00+00:00")
    return build_stop_update(holding or position(), frame if frame is not None else prices(), **kwargs)


def test_rising_ma_band_proposes_cent_floored_stop_and_auditable_values():
    frame = prices()
    frame.attrs["snapshots"] = [{"csv_sha256": "known-source"}]
    update = build(frame)
    assert update["new_stop"] == 114.65
    assert update["reason"] == "ma20_rising_support"
    assert update["policy_version"] == POLICY_VERSION
    assert update["source_completed_at"] == "2026-11-17T21:30:00+00:00"
    assert update["effective_at"] == update["observed_at"]
    evidence = update["evidence"]
    assert evidence["ma20"] == 114.75 and evidence["ema20"] > 114.75
    assert evidence["ma_band_low"] == 114.75 and evidence["atr"] == 1
    assert evidence["buffer_atr"] == .1 and evidence["buffer_value"] == pytest.approx(.1)
    assert evidence["selected_basis"] == "ma20"
    assert len(evidence["data_fingerprint"]) == 64
    assert evidence["source_snapshots"] == frame.attrs["snapshots"]
    assert frame.iloc[-1]["Close"] == 119.5


def test_no_higher_candidate_retains_current_stop_and_records_no_change():
    update = build(holding=position(stop=118))
    assert update["new_stop"] == 118
    assert update["reason"] == "no_higher_confirmed_support"
    assert update["evidence"]["ma_candidate"] < 118


def test_declining_band_already_breached_registers_future_protection_not_past_exit():
    frame = prices()
    close = pd.Series([120 - .2 * i for i in range(60)], index=frame.index)
    frame.loc[:, "Open"] = close - .1
    frame.loc[:, "High"] = close + .5
    frame.loc[:, "Low"] = close - .5
    frame.loc[:, "Close"] = close
    update = build(frame)
    assert update["new_stop"] > frame.iloc[-1]["Close"]
    assert update["reason"] == "ma20_breached_next_session"
    assert update["effective_at"] == "2026-11-17T23:30:00+00:00"
    assert "exit_price" not in update


def higher_low_prices():
    frame = prices()
    for column, offset in (("Open", -.1), ("High", .5), ("Low", -.5), ("Close", 0)):
        frame.loc[:, column] = [100 + offset] * 50 + [130 + offset] * 10
    frame.iloc[40, frame.columns.get_loc("Low")] = 90
    frame.iloc[53, frame.columns.get_loc("Low")] = 125
    return frame


def test_confirmed_post_entry_higher_low_can_outrank_ma_candidate():
    frame = higher_low_prices()
    entry_day = frame.index[50].date().isoformat()
    update = build(frame, holding=position(entry_date=entry_day))
    evidence = update["evidence"]
    assert evidence["selected_basis"] == "higher_low"
    assert evidence["pivot_low"] == 125 and evidence["previous_pivot_low"] == 90
    assert evidence["pivot_date"] == frame.index[53].date().isoformat()
    assert datetime.fromisoformat(evidence["pivot_confirmed_at"]).date() == frame.index[56].date()
    assert update["new_stop"] == evidence["structure_candidate"] > evidence["ma_candidate"]


def test_higher_low_requires_three_completed_right_hand_bars():
    frame = higher_low_prices()
    entry_day = frame.index[50].date().isoformat()
    source = frame.index[55].date().isoformat()
    before = build(frame, holding=position(entry_date=entry_day), source_as_of=source,
                   observed_at=source + "T23:30:00+00:00")
    assert before["evidence"]["structure_candidate"] is None
    source = frame.index[56].date().isoformat()
    after = build(frame, holding=position(entry_date=entry_day), source_as_of=source,
                  observed_at=source + "T23:30:00+00:00")
    assert after["evidence"]["structure_candidate"] is not None


def test_pivot_before_entry_is_not_repackaged_as_new_held_structure():
    update = build(higher_low_prices(), holding=position(entry_date="2026-11-17"))
    assert update["evidence"]["structure_candidate"] is None


def test_future_prices_do_not_change_plan_or_fingerprint():
    frame = prices()
    original = build(frame)
    frame.loc[pd.Timestamp("2026-11-18")] = [10000, 11000, -500, float("nan"), 1]
    later = build(frame)
    assert later == original


def test_update_identity_excludes_observation_time_and_existing_stop():
    original = build()
    later = build(holding=position(stop=118), observed_at="2026-11-18T00:00:00+00:00")
    assert later["update_id"] == original["update_id"]
    changed = prices()
    changed.iloc[0, changed.columns.get_loc("Volume")] += 1
    assert build(changed)["update_id"] != original["update_id"]


def test_source_before_entry_returns_no_update():
    assert build(holding=position(entry_date="2026-11-18")) is None


@pytest.mark.parametrize("observed", ["2026-11-17T21:29:59+00:00", "2026-11-17T21:30:00"])
def test_unfinished_or_timezone_naive_observation_is_rejected(observed):
    with pytest.raises(ValueError):
        build(observed_at=observed)


def test_observation_before_policy_activation_is_rejected():
    with pytest.raises(ValueError):
        build(enabled_at="2026-11-18T00:00:00+00:00")


def test_source_date_must_exist_and_have_enough_history():
    with pytest.raises(ValueError):
        build(prices().iloc[:-1])
    with pytest.raises(ValueError):
        build(prices().iloc[-20:])


def test_split_during_holding_is_rejected_but_future_event_is_ignored():
    frame = prices()
    frame.attrs["splits"] = [{"date": "2026-11-17", "numerator": 2, "denominator": 1}]
    with pytest.raises(ValueError, match="split"):
        build(frame)
    frame.attrs["splits"][0]["date"] = "2026-11-18"
    assert build(frame)["new_stop"] == 114.65


def test_malformed_price_history_is_not_silently_accepted():
    frame = prices()
    frame.iloc[-1, frame.columns.get_loc("Low")] = float("nan")
    with pytest.raises(ValueError):
        build(frame)


def test_less_than_one_cent_increase_is_recorded_as_no_change():
    update = build(holding=position(stop=114.645))
    assert update["new_stop"] == 114.645
    assert update["reason"] == "no_higher_confirmed_support"


def test_lower_low_is_not_reported_as_confirmed_rising_structure():
    frame = higher_low_prices()
    frame.iloc[53, frame.columns.get_loc("Low")] = 80
    update = build(frame, holding=position(entry_date=frame.index[50].date().isoformat()))
    assert update["evidence"]["pivot_low"] == 80
    assert update["evidence"]["previous_pivot_low"] == 90
    assert update["evidence"]["structure_candidate"] is None
    assert update["evidence"]["selected_basis"] == "ma20"


def test_source_completion_respects_new_york_daylight_saving_time():
    frame = prices()
    frame.index = pd.bdate_range(end="2026-07-01", periods=len(frame))
    update = build(frame, holding=position(entry_date="2026-06-30", entry_timestamp="2026-06-30T13:30:00+00:00"),
                   source_as_of="2026-07-01", observed_at="2026-07-01T21:00:00+00:00",
                   enabled_at="2026-06-01T00:00:00+00:00")
    assert update["source_completed_at"] == "2026-07-01T20:30:00+00:00"


def test_unknown_split_metadata_fails_as_a_source_error():
    frame = prices()
    frame.attrs["splits"] = None
    with pytest.raises(ValueError, match="split"):
        build(frame)
