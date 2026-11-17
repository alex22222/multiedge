"""Google 案例验证（PRD 验收标准 1-4）。"""
from pathlib import Path

import pandas as pd
import pytest

from multiedge.config import Config
from multiedge.data import load_daily
from multiedge.engine import analyze
from multiedge.indicators import weekly_sma, atr as atr_fn

DATA = Path(__file__).resolve().parent.parent / "data"
GOOGL = DATA / "GOOGL_2024-01-01_2025-08-08.csv"
SPX = DATA / "SPX_2024-01-01_2025-08-08.csv"


@pytest.fixture(scope="module")
def frames():
    return load_daily(GOOGL), load_daily(SPX)


def test_google_2025_08_01_buy(frames):
    """视频决策日 8/1：RS/趋势线/均线/量能成熟，水平结构=最后受阻高点 184.33（同带稳定
    Emerging）；趋势线与水平结构的价位接近 20MA(186.18)/7-21 低点(186.15)（≤0.75×ATR），
    不是「落在自己构成的 min–max 区内」的自指断言。"""
    df, spx = frames
    r = analyze(df, spx, "GOOGL", "2025-08-01", Config())
    assert r.verdict == "BUY"
    mature = {k for k, e in r.edges.items() if e["state"] == "Mature"}
    # 视频 5 边缘：RS/趋势线/均线/量能 Mature；水平结构=最后受阻高点（同带、
    # 稳定 Emerging——不追 Mature；BUY 由 均线+趋势线 双结构成带承载）
    assert {"momentum", "rs", "ma_zone", "volume_pattern", "trendline"} <= mature
    assert r.edges["sr_flip"]["state"] in ("Mature", "Emerging")
    # 绝对价位断言（对 20MA / 7-21 低点带）
    atrv = float(atr_fn(df.loc[:'2025-08-01']).iloc[-1])
    ma20 = 186.18
    tl = r.edges["trendline"]["level"]
    sr = r.edges["sr_flip"]["level"]
    assert abs(tl - ma20) <= 0.75 * atrv, f"趋势线 {tl} 不在 20MA/7-21 低点带"
    assert abs(sr - ma20) <= 0.75 * atrv, f"水平结构 {sr} 不在 20MA/7-21 低点带"
    # 水平结构（最后确认受阻高点 184.33）与 E1 止损参考锚（7/21 放量K线低点）是不同对象
    assert abs(sr - 184.33) <= 0.05, f"水平结构应为 7/16 受阻高点 184.33，实际 {sr}"
    assert r.stop_anchor is not None and abs(r.stop_anchor - 186.15) <= 0.05
    assert abs(sr - r.stop_anchor) > 1.0, "水平结构不得是止损锚的别名"
    assert len(r.structural_mature) >= 2
    # 止损口径：视频 7/21 低点 186.15 下方、约 1.6%~2.6%
    assert r.stop is not None and 0.016 <= r.stop_pct <= 0.026
    # 目标 = 前主要 swing 高（2025-02-04，207.05），RR 与视频 4~5R 对齐
    assert r.target == pytest.approx(207.05, abs=0.5)
    assert r.rr is not None and 3.5 <= r.rr <= 5.5
    assert r.weekly_regime_ok and r.pullback_age <= 10
    assert r.risks == []


def test_trendline_is_frozen_structural_line(frames):
    """趋势线必须是同一条几何对象贯穿回踩期：7/28→8/5 线值稳定爬升（固定斜率），
    不得跳到 190–195 去贴收盘价（第 3 轮的每日重选缺陷）。"""
    df, spx = frames
    days = ["2025-07-28", "2025-07-31", "2025-08-01", "2025-08-04", "2025-08-05"]
    rs = [analyze(df, spx, "GOOGL", d, Config()) for d in days]
    levels = [r.edges["trendline"]["level"] for r in rs]
    assert all(lv is not None for lv in levels)
    assert all(180 <= lv <= 189 for lv in levels), f"线值应稳定在 180–189 带，实际 {levels}"
    for a, b in zip(levels[:-1], levels[1:]):            # 相邻日推进 = 固定斜率，无重选跳变
        assert 0.0 < b - a < 0.6, f"相邻日线值跳变 {a}->{b}，疑似每日重选"
    assert abs(levels[2] - 186.18) <= 2.0                # 8/1 在 20MA 附近
    # 冻结点对与斜率输出：x 轴=交易日索引
    p1, p2, slope, _ = rs[2].trendline_frozen
    assert (p1, p2) == ("2025-05-22", "2025-06-30")
    assert 0.1 < slope < 0.25


def test_trendline_lookback_sensitivity(frames):
    """选线不得依赖'刚好包进 5/22'的窗口：初始窗口 20–200 变化（含用户实测的
    34/41/55/61/80/150 等平台外侧值），自动扩展必须核验出同一锚点 → 同一条线、
    同一 8/1 线值、判定不变。"""
    df, spx = frames
    for lb in (20, 34, 41, 42, 61, 80, 100, 150, 200):
        r = analyze(df, spx, "GOOGL", "2025-08-01", Config(trendline_lookback=lb))
        assert r.verdict == "BUY", f"lookback={lb} 判定不应改变"
        p1, p2, _, lv = r.trendline_frozen
        assert (p1, p2) == ("2025-05-22", "2025-06-30"), f"lookback={lb} 不应改变选线"
        assert abs(lv - 185.33) <= 0.01, f"lookback={lb} 线值 {lv} 偏离 185.33"


def test_trendline_anchor_grid_sensitivity(frames):
    """锚点结构规则（近R点新高+非fresh+决定性收复δ）在 R∈{3,4,5}×δ∈{0.5%..3%}
    网格上必须稳定：同一条线、同一线值、判定不变（含第 6 轮逃逸角 R=5,δ=2%）。"""
    df, spx = frames
    for rr in (3, 4, 5):
        for dl in (0.005, 0.01, 0.02, 0.03):
            r = analyze(df, spx, "GOOGL", "2025-08-01",
                        Config(trendline_anchor_r=rr, trendline_anchor_reclaim=dl))
            p1, p2, _, lv = r.trendline_frozen
            assert (p1, p2) == ("2025-05-22", "2025-06-30"), f"R={rr} δ={dl} 不应改变选线"
            assert abs(lv - 185.33) <= 0.01 and r.verdict == "BUY"


def test_trendline_degraded_not_silent(frames):
    """锚点不合格（跨段或不存在）时必须降级并带'低可信'标注，绝不静默换线：
    - leg_window=30 → 合法锚点 5/22 也因超窗被判跨段；
    - reclaim=0.50 → 无任何 pivot 通过决定性收复，走截断分支。"""
    df, spx = frames
    r1 = analyze(df, spx, "GOOGL", "2025-08-01", Config(trendline_leg_window=30))
    assert r1.edges["trendline"]["score"] <= 55
    assert any("低可信" in e for e in r1.edges["trendline"]["evidence"])
    r2 = analyze(df, spx, "GOOGL", "2025-08-01", Config(trendline_anchor_reclaim=0.50))
    assert r2.edges["trendline"]["score"] <= 55
    assert any("低可信" in e for e in r2.edges["trendline"]["evidence"])
    # δ=0：锚点退化为末点、HH 链不足 → 同一统一降级说明，不得静默 Absent
    r3 = analyze(df, spx, "GOOGL", "2025-08-01", Config(trendline_anchor_reclaim=0.0))
    assert r3.edges["trendline"]["score"] < 60
    assert any("低可信" in e for e in r3.edges["trendline"]["evidence"])


def test_nvda_generic_stop_and_target(frames):
    """非 Google 回归（NVDA @ 2025-11-14，历史新高区）：止损必须低于现价，
    否则明确报「止损锚不可用」并进 risks；目标不可得时 RR 明确不可评估。"""
    df = load_daily(DATA / "NVDA_2024-08-01_2025-11-14.csv")
    spx = load_daily(DATA / "SPX_2024-08-01_2025-11-14.csv")
    r = analyze(df, spx, "NVDA", "2025-11-14", Config())
    if r.stop is not None:
        assert r.stop < r.close, f"止损 {r.stop} 必须低于现价 {r.close}"
    else:
        assert any("止损锚不可用" in x for x in r.risks)
    if r.target is None:
        assert any("目标不可得" in x for x in r.risks)
        assert r.rr is None


def test_ma200_reported(frames):
    """视频 H1 的 200 日均线必须可机检输出：6/20 收在其下（'挣扎'阶段），8/1 收在其上。"""
    df, spx = frames
    r_jun = analyze(df, spx, "GOOGL", "2025-06-20", Config())
    r_aug = analyze(df, spx, "GOOGL", "2025-08-01", Config())
    for r in (r_jun, r_aug):
        assert r.ma200 and r.ma200 > 0
        assert r.dist_200ma_pct is not None
    assert r_jun.close < r_jun.ma200, "6/20 应收于 200DMA 下方（视频 H1）"
    assert r_aug.close > r_aug.ma200 * 1.03, "8/1 应明确站上 200DMA"


NO_BUY_DATES = [
    # 5 月杂讯反弹（旧扫描误报日）
    "2025-05-06", "2025-05-12", "2025-05-13", "2025-05-15", "2025-05-20",
    "2025-05-23", "2025-05-29", "2025-05-30",
    # 6 月落后阶段：Google 在 200MA 附近挣扎、无公司级催化（视频 39:35-40:38）
    *[d.date().isoformat() for d in pd.bdate_range("2025-06-02", "2025-06-30")],
    # 7/10 前 RS 线未重现，不应只因贴近 20MA 而买
    "2025-07-07", "2025-07-08", "2025-07-09", "2025-07-10",
    # 7/28 与 8/4、8/5：价格远离 META 汇合带（未回踩 20MA/趋势线/水平支撑同带），不应 BUY
    "2025-07-28", "2025-08-04", "2025-08-05",
]


@pytest.mark.parametrize("as_of", NO_BUY_DATES)
def test_scan_no_false_buy(frames, as_of):
    df, spx = frames
    r = analyze(df, spx, "GOOGL", as_of, Config())
    assert r.verdict != "BUY", f"{as_of} 不应出现 BUY（非 META 汇合带回踩）"


def test_weekly_regime_helper(frames):
    df, _ = frames
    w = weekly_sma(df["Close"], 30)
    assert w.dropna().iloc[-1] > 0
    assert df.loc["2025-08-01", "Close"] > w.loc["2025-08-01"]


def test_reproducible(frames):
    df, spx = frames
    r1 = analyze(df, spx, "GOOGL", "2025-08-01", Config())
    r2 = analyze(df, spx, "GOOGL", "2025-08-01", Config())
    assert r1.to_dict() == r2.to_dict()
