"""引擎：计算 7 条边缘 → MEA 多结构汇合 → 判定 BUY/WATCH/NO + 止损/目标/风险。

判定（PRD §3，多趋势验证）：
  BUY = 动能前提(新鲜 momentum ≥ Mature)
        + RS 成熟（视频 H3/H4：动能与 RS 是核心边缘）
        + 量能结构成熟（B6 高量突破+低量回调）
        + ≥2 条结构性边缘(均线区/水平结构/趋势线)成熟且 level 聚类（同一价位带，跨度≤mea_cluster_atr）
        + 现价位于 MEA 区内（到带中心 ≤ mea_atr_tolerance×ATR）
        + 动能峰后 ≤ first_pullback_max_days 日（B2 突破后第一次回调）
        + 周线 regime：收盘 ≥ 30 周 SMA（G1/G2/H1 高时间框架支配）
        + 无否定条件
  否则若动能存在且边缘萌芽 → WATCH；无动能 → NO。
"""
from dataclasses import dataclass, field, asdict

import numpy as np
import pandas as pd

from .config import Config
from .indicators import sma, atr, swing_pivots, weekly_sma, momentum_peak
from .edges import (momentum_edge, volume_pattern_edge, ma_zone_edge,
                    sr_flip_edge, trendline_edge, rs_edge, market_structure_edge)


@dataclass
class Report:
    ticker: str
    as_of: str
    close: float
    verdict: str                 # BUY / WATCH / NO
    edges: dict                  # key -> {score, state, evidence, level}
    mature_count: int            # 成熟边缘数（不含动能前提）
    mea_zone: list = field(default_factory=list)   # 多结构汇合区 [lo, hi]
    structural_mature: list = field(default_factory=list)
    trendline_frozen: list = field(default_factory=list)   # [p1, p2, slope_per_bar, 线值@T]
    stop_anchor: float | None = None    # E1 止损参考锚（7/21 放量突破K线低点）
    ma200: float | None = None
    dist_200ma_pct: float | None = None
    checklist: list = field(default_factory=list)   # 多趋势验证清单（渲染单一事实来源）
    chart: dict | None = None                       # with_chart=True 时的绘图数据
    pullback_age: int = 0        # 动能峰后交易日数
    weekly_regime_ok: bool = False
    stop: float | None = None
    stop_pct: float | None = None
    target: float | None = None
    rr: float | None = None
    risks: list = field(default_factory=list)
    disclaimers: str = "研究/回看验证工具；BUY 仅为系统规则下的初步信号判断，不构成投资建议。"

    def to_dict(self):
        return asdict(self)


def analyze(df, spx, ticker: str, as_of: str, cfg: Config | None = None,
            with_chart: bool = False) -> Report:
    cfg = cfg or Config()
    df = df.loc[:as_of].copy()
    spx = spx.loc[:as_of].copy()
    if len(df) < 60:
        raise ValueError("数据不足（<60 个交易日）")
    T = len(df) - 1
    c = df["Close"].values
    close = float(c[T])
    atrv = float(atr(df, cfg.atr_period).values[T])
    ph, pl = swing_pivots(df, cfg.pivot_k)

    # 动能峰（统一定义，见 indicators.momentum_peak）：最佳动量窗终点
    w = cfg.momentum_window
    peak_idx, _ = momentum_peak(df["Close"], T, w, cfg.momentum_lookback)
    pullback_age = T - peak_idx

    edges = {
        "momentum": momentum_edge(df, spx, cfg, peak_idx),
        "volume_pattern": volume_pattern_edge(df, spx, cfg, peak_idx),
        "ma_zone": ma_zone_edge(df, spx, cfg, atrv),
        "sr_flip": sr_flip_edge(df, spx, cfg, atrv, ph, peak_idx),
        "trendline": trendline_edge(df, spx, cfg, atrv, ph, peak_idx),
        "rs": rs_edge(df, spx, cfg),
        "market_structure": market_structure_edge(df, spx, cfg, ph),
    }

    structural = ["ma_zone", "sr_flip", "trendline"]
    mature_struct = [k for k in structural if edges[k].score >= cfg.mature_threshold]
    mature_all = [k for k, e in edges.items()
                  if k != "momentum" and e.score >= cfg.mature_threshold]

    # ---- MEA 汇合区：成熟结构边缘的 level 聚类（多结构同价位带）
    levels = sorted(edges[k].level for k in mature_struct if edges[k].level is not None)
    zone = None
    if levels:
        lo, hi = levels[0], levels[-1]
        center = (lo + hi) / 2
        if hi - lo <= cfg.mea_cluster_atr * atrv and abs(close - center) <= cfg.mea_atr_tolerance * atrv:
            zone = [round(lo, 2), round(hi, 2)]
    mea_active = zone is not None

    # ---- 周线 regime（观点 G1/G2/H1/H3）
    wsma = weekly_sma(df["Close"], cfg.weekly_regime_weeks)
    regime_ok = bool(close >= wsma.iloc[-1])
    ma200 = sma(df["Close"], cfg.ma200_period).iloc[-1]
    dist_200ma = float(close / ma200 - 1) if not np.isnan(ma200) else None

    # ---- 否定条件（PRD §4）
    risks = []
    if not np.isnan(wsma.iloc[-1]) and not regime_ok:
        risks.append(f"否定：收盘低于30周SMA（{wsma.iloc[-1]:.2f}），周线上升趋势未确立（G1/G2/H1）")
    spx_close = spx["Close"].reindex(df.index).ffill()
    spx_ret = spx_close.pct_change()
    spx_last_ret = float(spx_ret.iloc[-1])
    spx_3d = float((spx_close.iloc[-1] / spx_close.iloc[-4] - 1)) if len(spx_close) >= 4 else 0.0
    spx_ma50 = float(sma(spx_close, 50).iloc[-1])
    ma50 = sma(df["Close"], 50).iloc[-1]
    if (spx_last_ret <= cfg.mkt_crash_day or spx_3d <= cfg.mkt_crash_3d) and close < ma50:
        risks.append("否定：大盘急跌（当日≤-1.5% 或近3日累计≤-2.5%）且个股跌破50MA（B12/J7 反面）")
    if T - peak_idx >= 1 and "Volume" in df.columns:
        bv = df["Volume"].values[max(0, peak_idx - w + 1):peak_idx + 1].max()
        pv = df["Volume"].values[peak_idx + 1:T + 1][-3:].mean()
        if pv > bv:
            risks.append("否定：回调日均量大于突破climax量，'低量回调'不成立（B6 反面）")
    lo20 = float(df["Low"].values[max(0, T - 19):T + 1].min())
    if close <= lo20:
        risks.append("否定：收盘创20日新低，市场结构破坏（C6b 反面）")

    # ---- 催化剂检查（观点 B5/C1：动能须由公司级催化剂点燃）：
    # 动能窗内存在"相对跳空 ≥ catalyst_rel_gap"（个股跳空显著强于大盘 = 财报/公告级催化），
    # 或单日相对涨幅 ≥ 8%（小盘股无跳空时的催化剧涨）。
    so = spx["Open"].reindex(df.index).ffill().values
    sc = spx["Close"].reindex(df.index).ffill().values
    o = df["Open"].values
    r1 = df["Close"].pct_change().values
    rs1 = pd.Series(sc, index=df.index).pct_change().values
    lo_i = max(1, peak_idx - 20)
    catalyst = False
    catalyst_gap_date = None
    for i in range(lo_i, min(peak_idx, T) + 1):
        if (o[i] / c[i - 1] - so[i] / sc[i - 1]) >= cfg.catalyst_rel_gap:
            catalyst = True
            catalyst_gap_date = str(df.index[i].date())
            break
        if (r1[i] - rs1[i]) >= 0.08:
            catalyst = True

    # ---- 判定（多趋势验证）
    mom = edges["momentum"].score
    reasons = []
    if mom < cfg.mature_threshold:
        verdict = "NO"
        reasons.append("动能前提不满足：无新鲜 momentum move（B4）")
    else:
        if edges["rs"].score < cfg.mature_threshold:
            reasons.append(f"RS 未成熟（{edges['rs'].score:.1f} 分，视频 H3：无 RS 线不入场）")
        if edges["volume_pattern"].score < cfg.mature_threshold:
            reasons.append(f"量能结构未成熟（{edges['volume_pattern'].score:.1f} 分，B6）")
        if len(mature_struct) < 2 or not mea_active:
            reasons.append("结构性边缘未成带汇合（需≥2条成熟结构边缘且价位聚类，A1/B13）")
        if pullback_age > cfg.first_pullback_max_days:
            reasons.append(f"动能峰已过 {pullback_age} 日，非突破后首回调窗口（B2）")
        if not regime_ok:
            reasons.append("周线 regime 未通过（收盘<30周SMA，G1/G2）")
        if not catalyst:
            reasons.append("动能窗内无催化剂跳空/单日剧涨（B5：momentum move 须由催化剂点燃）")
        if risks:
            reasons.append("存在否定条件")
        verdict = "BUY" if not reasons else "WATCH"

    # ---- E1 止损参考锚（与水平结构分开的输出）：催化 gap 日前 leg 内最大成交量阳线低点
    anchor_low = anchor_date = None
    if "Volume" in df.columns:
        sc_v = df["Volume"].values
        o_v = df["Open"].values
        end_i = None
        so = spx["Open"].reindex(df.index).ffill().values
        scx = spx["Close"].reindex(df.index).ffill().values
        for i in range(max(1, peak_idx - 20), min(peak_idx, T) + 1):
            if (o_v[i] / c[i - 1] - so[i] / scx[i - 1]) > 0.02 and c[i] > c[i - 1]:
                end_i = i - 1
                break
        if end_i is None:
            end_i = min(peak_idx, T)
        seg = [i for i in range(max(1, end_i - 14), end_i) if c[i] > o_v[i]]
        if seg:
            anchor_i = max(seg, key=lambda i: sc_v[i])
            anchor_low = float(df["Low"].values[anchor_i])
            anchor_date = str(df.index[anchor_i].date())
    # ---- 止损：近端结构带（20MA区间/冻结趋势线/E1锚）下沿下方留缓冲；
    #      跌破即同时破坏多结构（视频：跌破 7/21 K 线 = 跌破 20MA+支撑区+趋势线）
    proximate = [edges[k].level for k in ("ma_zone", "trendline") if edges[k].level is not None]
    if anchor_low is not None:
        proximate.append(anchor_low)
    stop = stop_pct = None
    if proximate:
        candidate = min(proximate) - cfg.stop_buffer_atr * atrv
        if candidate < close:
            stop = candidate
            stop_pct = (close - stop) / close
        else:
            risks.append(f"止损锚不可用：近端结构带下沿 {candidate:.2f} 高于现价 {close:.2f}，"
                         "不输出负距离止损参考")
    # 目标：上一波主要 swing 高（peak 前 21~180 日最高 pivot 高，须高于现价≥2%）
    highs = df["High"].values
    tgt = None
    cands = [highs[p] for p in ph
             if peak_idx - 180 <= p <= peak_idx - 21 and highs[p] > close * 1.02]
    if cands:
        tgt = float(max(cands))
    if tgt is None:
        risks.append("目标不可得：peak 前 21~180 日内无高于现价 2% 的 pivot 高（如历史新高），"
                     "RR 不可评估")
    rr = ((tgt - close) / (close - stop)) if (tgt and stop and close - stop > 0) else None

    # ---- 多趋势验证清单（结构化，供报告渲染；数值全部来自本函数已算结果）
    negations = [x for x in risks if x.startswith("否定")]   # 止损/目标不可用不算否决
    checklist = [
        {"key": "momentum", "label": "动能前提（新鲜 momentum move）", "ok": bool(mom >= cfg.mature_threshold),
         "actual": f"{mom:.0f} 分", "threshold": f"≥{cfg.mature_threshold} 且峰龄≤{cfg.first_pullback_max_days}日（现 {pullback_age}）",
         "viewpoint": "B4 [12:53]"},
        {"key": "catalyst", "label": "公司级催化剂", "ok": bool(catalyst),
         "actual": f"相对跳空 {catalyst_gap_date}" if catalyst_gap_date else ("单日相对涨幅≥8%" if catalyst else "无"),
         "threshold": f"相对跳空≥{cfg.catalyst_rel_gap:.1%}", "viewpoint": "B5/C1 [12:19]"},
        {"key": "first_pullback", "label": "突破后首回调窗口", "ok": bool(pullback_age <= cfg.first_pullback_max_days),
         "actual": f"峰后 {pullback_age} 日", "threshold": f"≤{cfg.first_pullback_max_days} 日", "viewpoint": "B2 [02:57]"},
        {"key": "rs", "label": "相对强度 RS", "ok": bool(edges["rs"].score >= cfg.mature_threshold),
         "actual": f"{edges['rs'].score:.0f} 分", "threshold": f"≥{cfg.mature_threshold}", "viewpoint": "H3/H4 [40:31, 42:17]"},
        {"key": "volume_pattern", "label": "量能结构（高量突破+低量回调）", "ok": bool(edges["volume_pattern"].score >= cfg.mature_threshold),
         "actual": f"{edges['volume_pattern'].score:.0f} 分", "threshold": f"≥{cfg.mature_threshold}", "viewpoint": "B6/D2 [13:52]"},
        {"key": "structural", "label": "结构性边缘成带（MEA 多结构同价位带）", "ok": bool(len(mature_struct) >= 2 and mea_active),
         "actual": f"成熟结构 {len(mature_struct)} 条" + (f"，带 {zone}" if zone else "，未成带"),
         "threshold": "≥2 条成熟且聚类、现价在带内", "viewpoint": "A1/B13 [04:02, 82:39]"},
        {"key": "weekly_regime", "label": "周线 regime（高时间框架）", "ok": bool(regime_ok),
         "actual": f"收盘 {'≥' if regime_ok else '<'} 30周SMA", "threshold": "收盘 ≥ 30周SMA", "viewpoint": "G1/G2 [72:02]"},
        {"key": "no_negation", "label": "无否定条件", "ok": bool(not negations),
         "actual": f"{len(negations)} 条" if negations else "0 条", "threshold": "0（仅统计「否定：」开头的 J 系列项）", "viewpoint": "J 系列"},
    ]
    # ---- 图表数据（with_chart=True；全部来自本函数内的 df/结果，模板不重算）
    chart = None
    if with_chart:
        n_show = min(130, len(df))
        w0 = T - n_show + 1
        sub = df.iloc[w0:T + 1]
        tl_meta = edges["trendline"].meta
        slope = tl_meta.get("slope_per_bar")
        highs_all = df["High"].values
        # 锚点位置与 HH 链（链上落在窗口内的点，用于图上打点）
        anchor_pos = None
        if tl_meta.get("anchor"):
            hits = [i for i, d in enumerate(df.index) if str(d.date()) == tl_meta["anchor"]]
            anchor_pos = hits[0] if hits else None
        chain_pts = []
        if anchor_pos is not None:
            keep = []
            for p in ph[(ph >= anchor_pos) & (ph < peak_idx)]:
                if not keep or highs_all[p] >= highs_all[keep[-1]]:
                    keep.append(int(p))
            for p in keep:
                if p >= w0:
                    chain_pts.append([str(df.index[p].date()), round(float(highs_all[p]), 2)])
        # 冻结结构线：只从锚点起画（锚点之前留空，不在图上向左延伸）
        tl_vals = []
        for i in range(w0, T + 1):
            if anchor_pos is not None and slope is not None and i >= anchor_pos:
                tl_vals.append(round(float(highs_all[anchor_pos] + slope * (i - anchor_pos)), 2))
            else:
                tl_vals.append(None)
        # 量能：climax 日（峰前 10 日最大量）与回调末 3 日的窗口内位置
        climax_pos = pull_pos = None
        if "Volume" in df.columns:
            v_all = df["Volume"].values
            lo_w = max(0, peak_idx - 9)
            climax_pos = lo_w + int(np.argmax(v_all[lo_w:peak_idx + 1]))
            if T - peak_idx >= 1:
                last3 = list(range(peak_idx + 1, T + 1))[-3:]
                pull_pos = [p - w0 for p in last3 if p >= w0]
        ma200_s = sma(df["Close"], cfg.ma200_period)
        chart = {
            "dates": [str(d.date()) for d in sub.index],
            "open": [round(float(x), 2) for x in sub["Open"].values],
            "high": [round(float(x), 2) for x in sub["High"].values],
            "low": [round(float(x), 2) for x in sub["Low"].values],
            "close": [round(float(x), 2) for x in sub["Close"].values],
            "volume": [int(x) for x in sub["Volume"].values] if "Volume" in sub.columns else None,
            "ma10": [None if np.isnan(x) else round(float(x), 2) for x in sma(df["Close"], 10).iloc[w0:T + 1].values],
            "ma20": [None if np.isnan(x) else round(float(x), 2) for x in sma(df["Close"], 20).iloc[w0:T + 1].values],
            "ma50": [None if np.isnan(x) else round(float(x), 2) for x in sma(df["Close"], 50).iloc[w0:T + 1].values],
            "ma200": [None if np.isnan(x) else round(float(x), 2) for x in ma200_s.iloc[w0:T + 1].values],
            "tl": tl_vals,
            "sr_level": edges["sr_flip"].level,
            "mea_zone": zone,
            "stop": stop and round(stop, 2),
            "target": tgt and round(tgt, 2),
            "climax_pos": (climax_pos - w0) if climax_pos is not None and climax_pos >= w0 else None,
            "pullback_last3": pull_pos or [],
            "anchor_date": tl_meta.get("anchor"),
            "chain_points": chain_pts,
            "markers": {"peak": str(df.index[peak_idx].date()),
                        "catalyst_gap": catalyst_gap_date,
                        "breakout": tl_meta.get("cross"),
                        "anchor_bar": anchor_date},
            "frozen": {"p1": tl_meta.get("p1"), "p2": tl_meta.get("p2"),
                       "slope_per_bar": slope, "anchor": tl_meta.get("anchor"),
                       "chain": chain_pts},
        }
    return Report(
        ticker=ticker, as_of=str(df.index[T].date()), close=round(close, 2),
        verdict=verdict,
        edges={k: {"score": e.score, "state": e.state, "evidence": e.evidence, "level": e.level}
               for k, e in edges.items()},
        mature_count=len(mature_all), mea_zone=zone or [],
        structural_mature=mature_struct, pullback_age=int(pullback_age),
        weekly_regime_ok=regime_ok,
        trendline_frozen=[edges["trendline"].meta.get("p1"), edges["trendline"].meta.get("p2"),
                          edges["trendline"].meta.get("slope_per_bar"), edges["trendline"].level],
        stop_anchor=anchor_low and round(anchor_low, 2),
        ma200=ma200 and round(float(ma200), 2),
        dist_200ma_pct=dist_200ma and round(dist_200ma, 4),
        checklist=checklist, chart=chart,
        stop=stop and round(stop, 2), stop_pct=stop_pct and round(stop_pct, 4),
        target=tgt and round(tgt, 2), rr=rr and round(rr, 2),
        risks=risks + ([f"未通过：{r}" for r in reasons] if verdict != "BUY" else []),
    )
