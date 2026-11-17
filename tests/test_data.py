"""日期范围缓存必须能复用，并按截止日截断。"""
from pathlib import Path
from unittest.mock import patch

from multiedge.data import fetch_daily_chunked
from multiedge.dashboard import cached_frames


DATA = Path(__file__).resolve().parent.parent / "data"


def test_long_cache_reused_without_network_or_future_rows():
    with patch("urllib.request.urlopen", side_effect=AssertionError("不应联网")):
        df = fetch_daily_chunked("GOOGL", "2023-06-01", "2025-08-01", str(DATA))
    assert len(df) >= 330
    assert str(df.index[-1].date()) == "2025-08-01"
    assert df.index.is_unique


def test_new_short_cache_does_not_hide_older_replay_history(tmp_path):
    (tmp_path / "ABC_2025-01-01_2025-01-02.csv").write_text("Date,Open,High,Low,Close,Volume\n2025-01-01,100,102,99,101,10\n2025-01-02,101,103,100,102,11\n")
    (tmp_path / "ABC_2026-01-01_2026-01-02.csv").write_text("Date,Open,High,Low,Close,Volume\n2026-01-01,110,112,109,111,12\n2026-01-02,111,113,110,112,13\n")
    frames = cached_frames(["ABC"], tmp_path)
    assert len(frames["ABC"]) == 4
    assert "2025-01-01" in frames["ABC"].index
