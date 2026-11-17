"""报告渲染测试：单一事实来源（数字与 analyze() 一致）、自包含（无外部资源）、通用股票可用。"""
from pathlib import Path

import pytest

from multiedge.config import Config
from multiedge.data import load_daily
from multiedge.engine import analyze
from multiedge.report import render_report
from multiedge.report import render_snapshot

DATA = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture(scope="module")
def googl_report():
    df = load_daily(DATA / "GOOGL_2024-01-01_2025-08-08.csv")
    spx = load_daily(DATA / "SPX_2024-01-01_2025-08-08.csv")
    return render_report("GOOGL", "2025-08-01", data_dir=str(DATA))


def test_googl_report_numbers_match_analyze(googl_report):
    doc, r = googl_report
    assert r.verdict == "BUY"
    # 判定与关键数字来自 analyze() 输出本身
    assert f">{r.verdict}<" in doc
    tl = r.edges["trendline"]["level"]
    sr = r.edges["sr_flip"]["level"]
    assert f"{tl:.2f}" in doc and f"{sr:.2f}" in doc
    assert f"{r.stop:.2f}" in doc
    assert f"{r.target:.2f}" in doc
    assert f"{r.rr:.2f}R" in doc
    for needle in ("185.33", "184.33", "186.18", "207.05", "4.25", "多趋势验证清单", "操作化假设", "MEA"):
        assert needle in doc, f"报告缺少关键内容: {needle}"
    # Google 对照区
    assert "Google 案例对照" in doc and "9.4%" in doc and "4~5R" in doc


def test_chart_ma200_complete_and_tl_from_anchor(googl_report):
    """200MA 在整个图表窗口内不得为 None（长历史数据）；冻结 HH 线起点不得早于锚点。"""
    _, r = googl_report
    c = r.chart
    assert c["ma200"] and all(v is not None for v in c["ma200"])
    first_tl = next(i for i, v in enumerate(c["tl"]) if v is not None)
    first_tl_date = c["dates"][first_tl]
    assert c["anchor_date"] and first_tl_date >= c["anchor_date"], \
        f"HH 线从 {first_tl_date} 起画，早于锚点 {c['anchor_date']}"
    assert c["volume"] and len(c["volume"]) == len(c["dates"])
    assert c["climax_pos"] is not None and len(c["pullback_last3"]) == 3
    assert len(c["chain_points"]) >= 2


def test_svg_ma200_polyline_spans_full_window():
    """交付物级断言：HTML 里 stroke="#e2e8f0"（MA200）折线点数=窗口长度且从左侧开始。
    走 CLI 默认路径（不传 start，--start 默认应为长历史），杜绝「测试绿、交付红」。"""
    import re
    doc, r = render_report("GOOGL", "2025-08-01", data_dir=str(DATA))   # 不传 start = CLI 默认
    n = len(r.chart["dates"])
    m = re.search(r'stroke="#e2e8f0"[^/>]*points="([^"]+)"', doc) or \
        re.search(r'points="([^"]+)"[^/>]*stroke="#e2e8f0"', doc)
    assert m, "未找到 MA200 折线"
    pts = [p for p in m.group(1).split() if p.strip()]
    assert len(pts) == n, f"MA200 折线 {len(pts)} 点 ≠ 窗口 {n} 点"
    assert float(pts[0].split(",")[0]) < 80, f"MA200 起点 x={pts[0]} 不靠左"


def test_app_default_start_matches_report_long_start():
    """app 的默认 start 必须与 report 的 LONG_START 一致（第 12 轮防漏网）。"""
    import multiedge.app as app
    from multiedge.report import LONG_START
    assert app.DEFAULT_START == LONG_START == "2023-06-01"
    doc, r = render_report("GOOGL", "2025-08-01", data_dir=str(DATA), start=app.DEFAULT_START)
    assert r.chart["ma200"] and all(v is not None for v in r.chart["ma200"])


def test_no_band_report_marks_single_structure():
    """GOOGL 6/30：单一成熟结构不得标成零宽度「MEA 带 lo–hi」，应注明未成带。"""
    doc, r = render_report("GOOGL", "2025-06-30", data_dir=str(DATA))
    assert r.mea_zone and abs(r.mea_zone[0] - r.mea_zone[1]) < 1e-9   # 数据侧：上下沿相同
    assert "单一结构" in doc and "未成带" in doc
    assert f"MEA 带 {r.mea_zone[0]:.2f}–{r.mea_zone[1]:.2f}" not in doc
    assert 'opacity="0.12"' not in doc, "未成带不应画绿色带"


def test_report_has_no_external_resources(googl_report):
    doc, _ = googl_report
    assert 'src="http' not in doc and "href=\"http" not in doc and "url(http" not in doc
    assert "<svg" in doc, "图表必须为内联 SVG"


def test_googl_no_report_and_nvda_unavailable_stop():
    doc_no, r_no = render_report("GOOGL", "2025-06-30", data_dir=str(DATA))
    assert r_no.verdict == "NO" and "NO" in doc_no
    doc_nv, r_nv = render_report("NVDA", "2025-11-14", data_dir=str(DATA))
    assert "止损锚不可用" in doc_nv
    assert any("止损锚不可用" in x for x in r_nv.risks)
    # 止损/目标不可用不算「否定条件」：no_negation 只统计「否定：」项
    gate = [c for c in r_nv.checklist if c["key"] == "no_negation"][0]
    assert gate["actual"] == "0 条", f"否定条件应只统计真否定项，实际 {gate['actual']}"
    assert "止损锚不可用" not in doc_nv.split("风险 / 否定条件")[0].split("无否定条件")[-1]


def test_snapshot_report_identifies_its_actual_data_source():
    df = load_daily(DATA / "GOOGL_2023-06-01_2025-08-08.csv")
    spx = load_daily(DATA / "SPX_2023-06-01_2025-08-08.csv")
    r = analyze(df, spx, "GOOGL", "2025-08-01")
    doc, _ = render_snapshot(r, price_source="Yahoo chart OHLC Close; Adj Close unused")
    assert "Yahoo chart OHLC Close" in doc
    assert "数据：MarketWatch" not in doc
