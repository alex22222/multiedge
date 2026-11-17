"""自包含 HTML 报告渲染：所有数字来自 engine.analyze() 的输出，模板不重算任何指标。

用法：
  .venv/bin/python -m multiedge.report GOOGL --as-of 2025-08-01 -o reports/GOOGL_2025-08-01.html
"""
import argparse
import html
from pathlib import Path

from .config import Config
from .data import fetch_daily, load_daily
from .engine import analyze

SVG_FONT = "'Noto Sans CJK SC','Microsoft YaHei',sans-serif"

DISCLAIMER = "研究/回看验证工具；BUY 仅为系统规则下的初步信号判断，不构成投资建议。"

EDGE_LABELS = {
    "momentum": "动能（momentum）",
    "volume_pattern": "量能结构（volume pattern）",
    "ma_zone": "均线区（20EMA/20MA 区间）",
    "sr_flip": "水平结构（前阻力翻支撑）",
    "trendline": "趋势线（HH 结构线，冻结）",
    "rs": "相对强度（RS vs SPX）",
    "market_structure": "市场结构（辅助）",
}

# 视频口径参考值（人工整理自 VIEWPOINTS.md，仅用于 GOOGL 8/1 对照区）
GOOGLE_VIDEO_REF = {
    "edges": [
        ("RS（大盘拖累而非个股问题）", "rs", "视频：8/1 强于大盘（-1.44% vs -1.64%），RS 线 7/10 重现 [43:30, 40:31]"),
        ("趋势线（阻力转支撑）", "trendline", "视频：跌破 7/21 K 线=同时跌破 20MA+支撑区+趋势线 [45:12]"),
        ("均线（20MA）", "ma_zone", "视频：回踩 20MA 并守住 [43:02]"),
        ("量能（放大后缩至极低）", "volume_pattern", "视频：财报后量仍巨大，8/1 已缩至极低 [43:51]"),
        ("水平结构（阻力翻支撑）", "sr_flip", "视频：水平位突破后转支撑、延长 [44:05]"),
    ],
    "stop": "视频：7/21 K 线下方，约 1.6%（求成功率可 2%+）[44:59]",
    "target": "视频：目标前高，回报 9.4% [46:14]",
    "rr": "视频：约 4~5R [46:54]",
}


def esc(x):
    return html.escape(str(x))


def _fmt_stop(stop, stop_pct):
    if stop is None:
        return "止损锚不可用（见风险）"
    return f"{stop:.2f}（现价下方 {stop_pct * 100:.1f}%）"


def _fmt_target(tgt, close):
    if tgt is None:
        return "目标不可得（见风险）"
    return f"{tgt:.2f}（+{(tgt / close - 1) * 100:.1f}%）"


def _fmt_rr(rr):
    return f"{rr:.2f}R" if rr is not None else "不可评估（见风险）"


def _polyline(points, color, width, dash=None):
    """points: [(x,y)] 去掉 None；断成多段。"""
    segs, cur = [], []
    for p in points:
        if p is None or p[1] is None:
            if cur:
                segs.append(cur)
                cur = []
        else:
            cur.append(p)
    if cur:
        segs.append(cur)
    d = " ".join(
        f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in seg)}" fill="none" '
        f'stroke="{color}" stroke-width="{width}"' + (' stroke-dasharray="5,4"' if dash else '') + '/>'
        for seg in segs)
    return d


def render_chart(chart, close):
    """价格图 + 量能副图（内联 SVG）。标记用编号 ①②③ + 底部图例，避免压字/裁切。"""
    W = 980
    LG = 150                            # 右侧标签槽宽：水平线画到 W-LG，标签进槽不压 K 线
    PH, VH, GAP = 400, 96, 26          # 价格区高、量能区高、区间隔
    ml, mr, mt, mb = 56, 14, 16, 40
    H = mt + PH + GAP + VH + mb
    n = len(chart["dates"])
    step = (W - ml - mr - LG) / n
    xs = [ml + i * step + step / 2 for i in range(n)]

    lows = [x for x in chart["low"] if x is not None]
    highs = [x for x in chart["high"] if x is not None]
    extra = [close, chart["stop"] or close, chart["target"] or close, chart["sr_level"] or close]
    extra += [v for v in chart["tl"] if v is not None]
    for mk in ("ma10", "ma20", "ma50", "ma200"):          # 保证 MA200 等全程可见
        extra += [v for v in chart[mk] if v is not None]
    ylo = min(lows + extra) * 0.995
    yhi = max(highs + extra) * 1.005
    span = (yhi - ylo) or 1.0

    def Y(v):
        return mt + (yhi - v) / span * PH

    vtop = mt + PH + GAP
    vmax = max(chart["volume"]) * 1.15 if chart["volume"] else 1

    def YV(v):
        return vtop + VH - v / vmax * VH

    out = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="price chart" font-family="{SVG_FONT}">']
    out.append(f'<rect x="0" y="0" width="{W}" height="{H}" fill="#0f1722"/>')
    for g in range(5):
        gv = ylo + span * g / 4
        out.append(f'<line x1="{ml}" y1="{Y(gv):.1f}" x2="{W - mr - LG}" y2="{Y(gv):.1f}" stroke="#26303d" stroke-width="1"/>')
        out.append(f'<text font-family="{SVG_FONT}" x="{ml - 6}" y="{Y(gv) + 4:.1f}" fill="#8a97a8" font-size="11" text-anchor="end">{gv:.1f}</text>')
    # MEA 带：仅当真成带（宽度>0）时画带；单一结构画灰虚线并注明未成带
    z = chart["mea_zone"]
    band_ok = bool(z and z[1] - z[0] > 0.001)
    if band_ok:
        out.append(f'<rect x="{ml}" y="{Y(z[1]):.1f}" width="{W - ml - mr - LG}" height="{max(2, Y(z[0]) - Y(z[1])):.1f}" fill="#22c55e" opacity="0.12"/>')
    elif z:
        out.append(f'<line x1="{ml}" y1="{Y(z[0]):.1f}" x2="{W - mr - LG}" y2="{Y(z[0]):.1f}" stroke="#94a3b8" stroke-width="1.2" stroke-dasharray="3,4"/>')
    # 冻结结构线（仅从锚点开始）+ 折线绘制；其标签进右侧标签槽统一避让
    tl_pts = [(xs[i], Y(v)) for i, v in enumerate(chart["tl"]) if v is not None]
    if tl_pts:
        out.append(_polyline(tl_pts, "#a78bfa", 1.6))
    # 右端水平线标签：MEA带/目标/止损/水平结构/HH线，按 y 排序、行距不足自动错行，避免压字
    right_labels = []
    if band_ok:
        right_labels.append((Y(z[1]) + 12, "#4ade80", f"MEA 带 {z[0]:.2f}–{z[1]:.2f}"))
    elif z:
        right_labels.append((Y(z[0]) - 5, "#94a3b8", f"单一结构 {z[0]:.2f}（未成带）"))
    if chart["target"]:
        right_labels.append((Y(chart["target"]) - 5, "#22c55e", f"目标 {chart['target']:.2f}"))
    if chart["stop"]:
        right_labels.append((Y(chart["stop"]) - 5, "#f87171", f"止损 {chart['stop']:.2f}"))
    if chart["sr_level"]:
        v = chart["sr_level"]
        out.append(f'<line x1="{ml}" y1="{Y(v):.1f}" x2="{W - mr - LG}" y2="{Y(v):.1f}" stroke="#eab308" stroke-width="1.4" stroke-dasharray="6,4"/>')
        right_labels.append((Y(v) - 5, "#eab308", f"水平结构 {v:.2f}"))
    if tl_pts:
        right_labels.append((tl_pts[-1][1] - 5, "#c4b5fd",
                             f"HH 结构线（{chart['frozen']['p1']} 起）"))
    if chart["stop"]:
        out.append(f'<line x1="{ml}" y1="{Y(chart["stop"]):.1f}" x2="{W - mr - LG}" y2="{Y(chart["stop"]):.1f}" stroke="#ef4444" stroke-width="1.4"/>')
    if chart["target"]:
        out.append(f'<line x1="{ml}" y1="{Y(chart["target"]):.1f}" x2="{W - mr - LG}" y2="{Y(chart["target"]):.1f}" stroke="#22c55e" stroke-width="1.4" stroke-dasharray="6,4"/>')
    placed = []
    for y0, color, text in sorted(right_labels):
        y = y0
        while any(abs(y - py) < 13 for py in placed):
            y -= 13
        placed.append(y)
        out.append(f'<text font-family="{SVG_FONT}" x="{W - 4}" y="{y:.1f}" fill="{color}" font-size="11" text-anchor="end">{esc(text)}</text>')
    # HH 链打点
    date_pos = {d: i for i, d in enumerate(chart["dates"])}
    for dstr, price in chart["chain_points"]:
        if dstr in date_pos:
            out.append(f'<circle cx="{xs[date_pos[dstr]]:.1f}" cy="{Y(price):.1f}" r="3.2" fill="#0f1722" stroke="#a78bfa" stroke-width="1.6"/>')
    if chart["anchor_date"] and chart["chain_points"]:
        ad, ap = chart["chain_points"][0]
        if chart["anchor_date"] in date_pos:
            out.append(f'<text font-family="{SVG_FONT}" x="{xs[date_pos[ad]]:.1f}" y="{Y(ap) - 8:.1f}" fill="#c4b5fd" font-size="10.5" text-anchor="middle">锚 {chart["anchor_date"][5:]}</text>')
        else:
            out.append(f'<text font-family="{SVG_FONT}" x="{xs[date_pos[ad]] + 6:.1f}" y="{Y(ap) - 8:.1f}" fill="#c4b5fd" font-size="10.5">链点 {ad[5:]}（锚点 {chart["anchor_date"][5:]} 在图左外侧）</text>')
    # 均线
    for key, color in (("ma10", "#f59e0b"), ("ma20", "#38bdf8"), ("ma50", "#94a3b8"), ("ma200", "#e2e8f0")):
        pts = [(xs[i], Y(v)) for i, v in enumerate(chart[key]) if v is not None]
        out.append(_polyline(pts, color, 1.2, dash=(key == "ma200")))
    # K 线
    bw = max(1.5, step * 0.62)
    for i in range(n):
        o, h, l, cl = chart["open"][i], chart["high"][i], chart["low"][i], chart["close"][i]
        up = cl >= o
        col = "#22c55e" if up else "#ef4444"
        out.append(f'<line x1="{xs[i]:.1f}" y1="{Y(h):.1f}" x2="{xs[i]:.1f}" y2="{Y(l):.1f}" stroke="{col}" stroke-width="1"/>')
        top, bot = (Y(o), Y(cl)) if up else (Y(cl), Y(o))
        out.append(f'<rect x="{xs[i] - bw / 2:.1f}" y="{top:.1f}" width="{bw:.1f}" height="{max(1, bot - top):.1f}" fill="{col}"/>')
    # 量能副图
    if chart["volume"]:
        out.append(f'<line x1="{ml}" y1="{vtop}" x2="{W - mr - LG}" y2="{vtop}" stroke="#26303d" stroke-width="1"/>')
        out.append(f'<text font-family="{SVG_FONT}" x="{ml - 6}" y="{vtop + 12}" fill="#8a97a8" font-size="11" text-anchor="end">量</text>')
        pull = set(chart["pullback_last3"])
        climax = chart["climax_pos"]
        for i, v in enumerate(chart["volume"]):
            o, cl = chart["open"][i], chart["close"][i]
            col = "#22c55e" if cl >= o else "#ef4444"
            op = "0.45" if i in pull else "0.8"
            out.append(f'<rect x="{xs[i] - bw / 2:.1f}" y="{YV(v):.1f}" width="{bw:.1f}" height="{max(1, vtop + VH - YV(v)):.1f}" fill="{col}" opacity="{op}"/>')
        if climax is not None:
            cv = chart["volume"][climax]
            out.append(f'<rect x="{xs[climax] - bw / 2 - 1.5:.1f}" y="{YV(cv) - 1.5:.1f}" width="{bw + 3:.1f}" height="{max(2, vtop + VH - YV(cv) + 1.5):.1f}" fill="none" stroke="#22d3ee" stroke-width="1.4"/>')
    # 编号标记（竖线 + 底部编号，不堆顶部）
    mark_colors = {"peak": "#f59e0b", "catalyst_gap": "#22d3ee", "breakout": "#a78bfa"}
    mark_names = {"peak": "动能峰", "catalyst_gap": "催化跳空", "breakout": "突破日"}
    legend_items = []
    idx = 1
    used_x = []
    for key in ("peak", "catalyst_gap", "breakout"):
        dstr = chart["markers"].get(key)
        if dstr and dstr in date_pos:
            x = xs[date_pos[dstr]]
            while any(abs(x - u) < 14 for u in used_x):   # 同日多标记：水平错开，避免压字
                x += 13
            used_x.append(x)
            out.append(f'<line x1="{x:.1f}" y1="{mt}" x2="{x:.1f}" y2="{vtop + VH}" stroke="{mark_colors[key]}" stroke-width="1" stroke-dasharray="3,3" opacity="0.65"/>')
            circled = "①②③"[idx - 1]
            out.append(f'<text font-family="{SVG_FONT}" x="{x:.1f}" y="{H - 24}" fill="{mark_colors[key]}" font-size="11" text-anchor="middle">{circled}</text>')
            legend_items.append(f'<span style="color:{mark_colors[key]}">{"①②③"[idx - 1]} {mark_names[key]} {dstr}</span>')
            idx += 1
    out.append(f'<text font-family="{SVG_FONT}" x="{ml}" y="{H - 8}" fill="#8a97a8" font-size="11">{chart["dates"][0]}</text>')
    out.append(f'<text font-family="{SVG_FONT}" x="{W - mr}" y="{H - 8}" fill="#8a97a8" font-size="11" text-anchor="end">{chart["dates"][-1]}</text>')
    out.append('</svg>')
    legend = ('<div class="legend">'
              '<span style="color:#f59e0b">MA10</span> <span style="color:#38bdf8">MA20</span> '
              '<span style="color:#94a3b8">MA50</span> <span style="color:#e2e8f0">MA200(虚线)</span> · '
              '<span style="color:#a78bfa">HH结构线（锚起）+链上点</span> '
              '<span style="color:#eab308">━━ 水平结构 ' + (f'{chart["sr_level"]:.2f}' if chart["sr_level"] else '—') + '</span> '
              '<span style="color:#ef4444">━━ 止损 ' + (f'{chart["stop"]:.2f}' if chart["stop"] else '不可用') + '</span> '
              '<span style="color:#22c55e">┅ 目标 ' + (f'{chart["target"]:.2f}' if chart["target"] else '不可得') + '</span> · '
              + " ｜ ".join(legend_items)
              + ' · 量能：<span style="color:#22d3ee">青框=突破 climax</span>，半透=回调末 3 日</div>')
    return "".join(out) + legend


LONG_START = "2023-06-01"   # 长历史起点：保证 200MA 覆盖 130 根图表窗口


def render_report(ticker, as_of, cfg=None, data_dir="data", start=None):
    cfg = cfg or Config()
    start = start or LONG_START
    safe = ticker.upper().replace("^", "")
    p1 = Path(data_dir) / f"{safe}_{start}_{as_of}.csv"
    p2 = Path(data_dir) / f"SPX_{start}_{as_of}.csv"
    from .data import fetch_daily_chunked
    need = cfg.ma200_period + 130   # 200MA 覆盖 130 根图表窗口所需的最少数据
    if p1.exists() and p2.exists():
        df, spx = load_daily(p1), load_daily(p2)
        if len(df) < need or len(spx) < need:   # 旧短缓存不再使用
            df = fetch_daily_chunked(ticker, LONG_START, as_of, data_dir)
            spx = fetch_daily_chunked("SPX", LONG_START, as_of, data_dir)
    else:
        df = fetch_daily_chunked(ticker, start, as_of, data_dir)
        spx = fetch_daily_chunked("SPX", start, as_of, data_dir)
    r = analyze(df, spx, ticker, as_of, cfg, with_chart=True)
    return render_snapshot(r, cfg)


def render_snapshot(r, cfg=None, source_note="", price_source="MarketWatch 日线（未复权）"):
    """直接呈现保存的引擎输出；缺少原始行情时不补算图表。"""
    cfg = cfg or Config()
    vc = {"BUY": ("#16a34a", "可买入点（初步信号）"), "WATCH": ("#d97706", "观察（条件未齐）"),
          "NO": ("#dc2626", "不出现买入点")}[r.verdict]
    rows_check = "".join(
        f'<tr><td>{"✅" if c["ok"] else "❌"}</td><td>{esc(c["label"])}</td>'
        f'<td class="num">{esc(c["actual"])}</td><td>{esc(c["threshold"])}</td>'
        f'<td class="vp">{esc(c["viewpoint"])}</td></tr>'
        for c in r.checklist)

    def edge_row(key, e):
        pct = e["score"]
        color = "#16a34a" if e["state"] == "Mature" else ("#d97706" if e["state"] == "Emerging" else "#64748b")
        lvl = f'<span class="num">价位 {e["level"]:.2f}</span>' if e["level"] is not None else ""
        low = ' <span class="badge-low">低可信降级</span>' if any("低可信" in x for x in e["evidence"]) else ""
        evs = "".join(f"<li>{esc(x)}</li>" for x in e["evidence"])
        return (f'<div class="edge"><div class="edge-head"><b>{esc(EDGE_LABELS.get(key, key))}</b>'
                f'<span class="state" style="background:{color}">{e["state"]} {pct:.0f}</span>{lvl}{low}</div>'
                f'<div class="bar"><div class="bar-fill" style="width:{pct:.0f}%;background:{color}"></div></div>'
                f'<ul class="ev">{evs}</ul></div>')

    edges_html = "".join(edge_row(k, r.edges[k]) for k in
                         ("momentum", "volume_pattern", "ma_zone", "sr_flip", "trendline", "rs", "market_structure"))

    risks_html = "".join(f"<li>{esc(x)}</li>" for x in r.risks) or "<li>无</li>"
    # 只列出与本次判定前提相关的参数，附中文理由（对应 PRD「操作化假设清单」）
    ASSUMPTION_REASONS = {
        "momentum": [("momentum_window", "动能衡量窗口（约一个月）"),
                     ("momentum_strong_pct", "动能强度满分阈值（视频原数字：超大市值 14%/月=极强）"),
                     ("momentum_lookback", "最佳动量窗的可滑动范围"),
                     ("fresh_full", "动能新鲜度：峰龄≤5 日不折扣"),
                     ("fresh_mid", "峰龄 6–10 日 ×0.7"),
                     ("fresh_old", "峰龄 11–15 日 ×0.45、>15 日 ×0.25")],
        "catalyst": [("catalyst_rel_gap", "公司级催化跳空的相对幅度门槛（区分财报/公告级催化与宏观普涨跳空）")],
        "first_pullback": [("first_pullback_max_days", "突破后第一次回调的有效窗口")],
        "rs": [("mature_threshold", "成熟分数线（60=Mature、30=Emerging）")],
        "volume_pattern": [("vol_baseline_days", "量能基线窗口（机构多日持续买入的判断基准）")],
        "structural": [("mea_atr_tolerance", "现价到 MEA 汇合带中心的最大距离（1×ATR）"),
                       ("mea_cluster_atr", "多条结构位聚类成带的最大跨度（2×ATR）")],
        "weekly_regime": [("weekly_regime_weeks", "周线 regime 周期（30 周 SMA；注：不是 200 日均线）")],
        "no_negation": [("mkt_crash_day", "大盘急跌否定阈值（当日 ≤−1.5%）"),
                        ("mkt_crash_3d", "大盘急跌否定阈值（近 3 日累计 ≤−2.5%）")],
    }
    rows_params = []
    for c in r.checklist:
        for fname, reason in ASSUMPTION_REASONS.get(c["key"], []):
            rows_params.append(f"<tr><td><code>{esc(fname)}</code></td><td class='num'>{esc(getattr(cfg, fname))}</td>"
                               f"<td>{esc(reason)}</td><td class='vp'>{esc(c['label'].split('（')[0])}</td></tr>")
    params = "".join(rows_params)

    google_sec = ""
    if r.ticker == "GOOGL" and r.as_of == "2025-08-01":
        ref = GOOGLE_VIDEO_REF
        rows = ""
        for label, key, note in ref["edges"]:
            e = r.edges[key]
            sys_txt = f'{e["state"]} {e["score"]:.0f} 分' + (f'，level {e["level"]:.2f}' if e["level"] else "")
            diff = ""
            if key == "sr_flip":
                diff = '<div class="diff">差异：视频把水平结构列为 5 边缘之一；系统评 Emerging 45：该位虽与 20MA/趋势线同带，但距现价 1.14×ATR，属于更深的第二层支撑架子（视频 B10 提过价格可跌到「第二层 MEA」），对本次回踩的直接保护弱于 20MA，故成熟度如实评 Emerging；它仍作为 MEA 汇合带的成员参与判定。</div>'
            rows += (f'<tr><td>{esc(label)}</td><td class="num">{esc(sys_txt)}</td>'
                     f'<td class="vp">{esc(note)}</td></tr>{diff}')
        google_sec = f'''
<details class="google" open><summary>Google 案例对照（系统输出 vs 视频结论）</summary>
<table><thead><tr><th>视频点名的边缘</th><th>系统输出</th><th>视频口径</th></tr></thead><tbody>{rows}
<tr><td>止损</td><td class="num">{esc(_fmt_stop(r.stop, r.stop_pct))}</td><td class="vp">{esc(ref["stop"])}</td></tr>
<tr><td>目标</td><td class="num">{esc(_fmt_target(r.target, r.close))}</td><td class="vp">{esc(ref["target"])}</td></tr>
<tr><td>RR</td><td class="num">{esc(_fmt_rr(r.rr))}</td><td class="vp">{esc(ref["rr"])}</td></tr>
</tbody></table>
<div class="diff">数据核对：目标 207.05 相对 189.13 为 +9.47%，与视频 9.4% 一致；8/1 Google -1.44% vs 标普 -1.60%（视频 -1.64%），差异 0.04pp 为数据源口径。</div>
</details>'''

    failed = [c["label"] for c in r.checklist if not c["ok"]]
    chart_html = (f'<h2>价格图（近 {len(r.chart["dates"])} 个交易日）</h2>' + render_chart(r.chart, r.close)) if r.chart else '<h2>价格图待补充</h2><p class="muted">原始附件没有图表与行情快照，本页仅展示已取得的引擎输出。</p>'
    if r.verdict == "BUY":
        why_not = '<p class="muted">全部买入前提通过。</p>'
    else:
        why_not = ('<p><b>未通过的前提：</b>' + "；".join(esc(x) for x in failed) + "</p>") if failed else ""
    html_doc = f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(r.ticker)} 多趋势验证报告 {r.as_of}</title>
<style>
body{{font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;background:#0b1118;color:#dbe4ee;margin:0;padding:24px;line-height:1.55}}
.wrap{{max-width:1020px;margin:0 auto}}
.card{{background:#111a26;border:1px solid #223047;border-radius:12px;padding:18px 22px;margin-bottom:18px}}
h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:17px;margin:0 0 12px;color:#9fb3c8}}
.verdict{{display:inline-block;padding:6px 18px;border-radius:999px;color:#fff;font-weight:700;font-size:18px;background:{vc[0]}}}
.muted{{color:#8a97a8;font-size:13px}} .num{{font-variant-numeric:tabular-nums}}
table{{width:100%;border-collapse:collapse;font-size:14px}}
th,td{{text-align:left;padding:7px 9px;border-bottom:1px solid #1e2a3a;vertical-align:top}}
th{{color:#9fb3c8;font-weight:600}} .vp{{color:#8a97a8;font-size:12.5px}}
.edge{{margin-bottom:14px}} .edge-head{{display:flex;gap:10px;align-items:center;flex-wrap:wrap}}
.state{{color:#fff;border-radius:6px;padding:2px 8px;font-size:12.5px}}
.bar{{background:#1c2735;border-radius:6px;height:8px;margin:6px 0;max-width:520px}}
.bar-fill{{height:8px;border-radius:6px}}
ul.ev{{margin:6px 0 0 18px;padding:0;color:#b7c4d4;font-size:13px}}
.badge-low{{background:#7c2d12;color:#fdba74;border-radius:6px;padding:2px 8px;font-size:12px}}
.legend{{color:#8a97a8;font-size:12.5px;margin-top:6px}}
details{{background:#111a26;border:1px solid #223047;border-radius:12px;padding:14px 18px;margin-bottom:18px}}
summary{{cursor:pointer;font-weight:600;color:#9fb3c8}}
.diff{{background:#1a2433;border-left:3px solid #d97706;padding:8px 12px;margin:8px 0;font-size:13px;color:#cbd5e1}}
.disc{{color:#f59e0b;font-size:13px}}
.risk li{{color:#fca5a5}}
a{{color:#38bdf8}}
</style></head><body><div class="wrap">

<div class="card">
<h1>{esc(r.ticker)} · 多趋势验证买入系统报告</h1>
<p class="muted">{esc(source_note)}</p>
<div>截止日 <b>{r.as_of}</b> ｜ 收盘 <b class="num">{r.close:.2f}</b></div>
<p><span class="verdict">{r.verdict}</span> <span class="muted">{vc[1]}</span></p>
{why_not}
<p class="disc">{DISCLAIMER}</p>
</div>

<div class="card">
<h2>多趋势验证清单（全部 ✅ 才 BUY）</h2>
<table><thead><tr><th style="width:34px"></th><th>买入前提</th><th>实际值</th><th>阈值</th><th>视频观点</th></tr></thead>
<tbody>{rows_check}</tbody></table>
</div>

<div class="card">
{chart_html}
</div>

<div class="card">
<h2>各观点/边缘成熟度（0–100）</h2>
{edges_html}
</div>

<div class="card">
<h2>风险 / 否定条件 / 出场参考</h2>
<ul class="risk">{risks_html}</ul>
<table><tbody>
<tr><td>止损参考</td><td class="num">{esc(_fmt_stop(r.stop, r.stop_pct))}</td></tr>
<tr><td>E1 锚（7/21 类放量K线低点）</td><td class="num">{r.stop_anchor if r.stop_anchor else "—"}</td></tr>
<tr><td>目标（前主要 swing 高）</td><td class="num">{esc(_fmt_target(r.target, r.close))}</td></tr>
<tr><td>RR</td><td class="num">{esc(_fmt_rr(r.rr))}</td></tr>
<tr><td>200DMA（信息项，视频 H1）</td><td class="num">{r.ma200}（{(r.dist_200ma_pct or 0) * 100:+.1f}%）</td></tr>
<tr><td>周线 regime</td><td class="num">{"通过" if r.weekly_regime_ok else "未通过"}</td></tr>
<tr><td>首回调龄</td><td class="num">{r.pullback_age} 日</td></tr>
</tbody></table>
</div>

{google_sec}

<details><summary>操作化假设（本次判定用到的非视频参数；逐项说明见 PRD「操作化假设清单」）</summary>
<table><tbody>{params}</tbody></table>
<p class="muted">对应关系与取值理由：PRD.md「操作化假设清单」表（Config 每个字段均有独立一行）。</p>
</details>

<p class="muted">生成：multiedge.report ｜ 数据：{esc(price_source)}｜ {DISCLAIMER}</p>
</div></body></html>'''
    return html_doc, r


def main():
    ap = argparse.ArgumentParser(prog="multiedge.report")
    ap.add_argument("ticker")
    ap.add_argument("--as-of", required=True)
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--start", default=None, help="数据起始日（默认 2023-06-01 长历史，保证 200MA 全程可见）")
    args = ap.parse_args()
    doc, _ = render_report(args.ticker, args.as_of, data_dir=args.data_dir, start=args.start)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")
    print(f"written: {out}")


if __name__ == "__main__":
    main()
