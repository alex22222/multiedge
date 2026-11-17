"""7 条边缘的打分实现。每条返回 EdgeResult(score, state, evidence)。

约定：df / spx 均已截取到 as_of（最后一行=分析日 T），索引位置 T = len(df)-1。
"""
from dataclasses import dataclass, field
import numpy as np
import pandas as pd

from .config import Config
from .indicators import sma, ema, atr, swing_pivots, rs_ratio


@dataclass
class EdgeResult:
    key: str
    score: float
    state: str
    evidence: list = field(default_factory=list)
    level: float | None = None   # 结构位（支撑线值），供 MEA 汇合判定
    meta: dict = field(default_factory=dict)   # 额外可机检输出（如冻结线点对/斜率）


def _clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


# ---------------------------------------------------------------- 动能（B4/B5/H3）
def momentum_edge(df, spx, cfg: Config, peak_idx: int) -> EdgeResult:
    """peak_idx 由 engine 用 indicators.momentum_peak 统一计算（最佳动量窗终点），
    保证首回调龄/催化剂窗/量能 climax 与动能评分引用同一个峰。"""
    T = len(df) - 1
    c = df["Close"].values
    w = cfg.momentum_window
    best_t = min(peak_idx, T)
    best_r = np.log(c[best_t] / c[best_t - w])
    pct = np.exp(best_r) - 1
    score = 100.0 * min(1.0, pct / cfg.momentum_strong_pct)
    ev = [f"最佳{w}日窗口截至 {df.index[best_t].date()}: 涨幅 {pct:+.1%}"
          f"（阈值 {cfg.momentum_strong_pct:.0%} 记 100 分）"]
    # 新鲜度：动能必须是"最近的"move（B4：先看到 momentum move 再找其后 MEA）
    age = T - best_t
    factor = 1.0 if age <= 5 else (0.7 if age <= 10 else (0.45 if age <= 15 else 0.25))
    if factor < 1.0:
        ev.append(f"动能峰距今 {age} 个交易日，新鲜度折扣 ×{factor}")
    score *= factor

    # 催化跳空：开盘相对前收跳空>2% 即计（财报 gap up 是典型催化剂，B5）
    o = df["Open"].values
    gaps = [(df.index[i].date(), o[i] / c[i - 1] - 1) for i in range(max(1, T - w + 1), T + 1)
            if o[i] / c[i - 1] - 1 > 0.02]
    if gaps:
        score += 10
        ev.append("催化跳空高开: " + ", ".join(f"{d} {g:+.1%}" for d, g in gaps[:3]))

    if "Volume" in df.columns:
        v = df["Volume"].values
        base = v[max(0, best_t - w - cfg.vol_baseline_days):best_t - w].mean()
        upv = v[best_t - w + 1:best_t + 1].mean()
        if base > 0:
            ratio = upv / base
            score += 15 if ratio >= 1.3 else (5 if ratio >= 1.0 else 0)
            ev.append(f"动能段均量/前{cfg.vol_baseline_days}日均量 = {ratio:.2f}")

    score = _clamp(score)
    return EdgeResult("momentum", round(score, 1), cfg.state(score), ev)


# ---------------------------------------------------------------- 量能结构（B6/D2）
def volume_pattern_edge(df, spx, cfg: Config, peak_idx: int) -> EdgeResult:
    T = len(df) - 1
    v = df["Volume"].values
    w = cfg.momentum_window
    ev = []
    if T - peak_idx < 1:
        return EdgeResult("volume_pattern", 35.0, cfg.state(35.0),
                          ["动能见顶后尚无回调日，量能结构待确认"], None)
    up = v[max(0, peak_idx - 9):peak_idx + 1]      # 突破 surge：峰前 10 日内的 climax 单日量（B6）
    climax_vol = up.max()
    last3 = v[peak_idx + 1:T + 1][-3:]             # 回调段最后 3 日均量（"到8/1已缩至极低"）
    pullback_vol = last3.mean()
    base = v[max(0, peak_idx - 9 - cfg.vol_baseline_days):peak_idx - 9].mean()
    ratio = climax_vol / pullback_vol if pullback_vol > 0 else 9.9
    score = _clamp(55 * np.log2(max(ratio, 1.0)))
    ev.append(f"突破climax量 {climax_vol:,.0f} / 回调末3日均量 {pullback_vol:,.0f} = {ratio:.2f}")
    if base > 0 and climax_vol / base >= 1.5:
        score += 20
        ev.append(f"climax 量能高于基线 {climax_vol / base:.2f} 倍")
    if base > 0 and (up > 1.5 * base).sum() >= 2:
        score += 10
        ev.append("上涨段≥2日放量超基线1.5倍（机构多日持续买入，D2）")
    score = _clamp(score)
    return EdgeResult("volume_pattern", round(score, 1), cfg.state(score), ev)


# ---------------------------------------------------------------- 均线区间（B2/B13/H8）
def ma_zone_edge(df, spx, cfg: Config, atrv: float) -> EdgeResult:
    T = len(df) - 1
    c = df["Close"].values
    ma10 = sma(df["Close"], 10).values
    e20 = ema(df["Close"], 20).values
    m20 = sma(df["Close"], 20).values
    ma50 = sma(df["Close"], 50).values
    lo = min(e20[T], m20[T])
    hi = max(e20[T], m20[T])
    # 价格到 20EMA/20MA 区间的距离（区间外为到较近端点的距离）
    if lo <= c[T] <= hi:
        d = 0.0
    else:
        d = min(abs(c[T] - lo), abs(c[T] - hi)) / atrv
    base = 80.0 if d <= 0.5 else (60.0 if d <= 1.0 else (35.0 if d <= 1.5 else 10.0))
    ev = [f"收盘 {c[T]:.2f}，20MA区间 [{lo:.2f}, {hi:.2f}]（距离 {d:.2f}×ATR），10MA {ma10[T]:.2f}"]
    # 近3日是否回踩/触及 20MA 区间后收回其上方（测试支撑）
    tested = False
    for i in range(max(20, T - 2), T + 1):
        if df["Low"].values[i] <= hi * 1.005 and c[i] >= lo:
            tested = True
    if tested:
        base += 10
        ev.append("近3日低点进入20MA区间上沿+0.5%缓冲带并收回区间上方（支撑测试，非精确触及）")
    if not np.isnan(ma50[T]) and c[T] < ma50[T]:
        base -= 25
        ev.append("收盘价已跌破50MA（扣分）")
    score = _clamp(base)
    level = (lo + hi) / 2
    return EdgeResult("ma_zone", round(score, 1), cfg.state(score), ev, level=float(level))


# ---------------------------------------------------------------- 水平结构：前阻力翻支撑（H5⑤）
def sr_flip_edge(df, spx, cfg: Config, atrv: float, pivot_highs: np.ndarray,
                 peak_idx: int) -> EdgeResult:
    """水平结构 = 催化 gap 前**最后一次明确受阻的高点**（former resistance → support）。

    规则（无聚类容差参数，"受阻"由次日未创新高确认，是结构事实不是阈值）：
    1. 催化 gap 日（动能窗内首个相对跳空>2% 的阳线）前 [gap−10, gap) 窗口内，
       从近到远找第一个"确认受阻高点" h[i]：**次日日内高点低于它**
       （h[i+1] < h[i]，反弹在此终结）。Google 案例：7/16 高 184.33，
       7/17 高 184.06 未创新高 → 受阻确认（7/18 高 186.42 次日 7/21 续创新高，
       是启动点而非受阻点，不入选）；
    2. 该位须被向上突破（首个收盘 > 该位的交易日 = 突破日，7/18），
       且**突破日起逐日收盘核验未跌破**（已跌破则 Absent 并如实写明）；
    3. 距现价按 ATR 打分。

    与 E1 止损参考锚（7/21 放量K线低点，engine 独立输出）是**两个不同对象**：
    本边缘 = 184.33 前阻力（距 20MA 0.43×ATR，同带）；止损锚 = 186.15。
    若窗口内无任何确认受阻高点，回退到"近 90 日被突破的前 swing 高"候选。
    """
    T = len(df) - 1
    c = df["Close"].values
    o = df["Open"].values
    h = df["High"].values
    k = cfg.pivot_k
    ev = []
    candidates = []   # (level, breakout_idx, kind, broken)

    so = spx["Open"].reindex(df.index).ffill().values
    scx = spx["Close"].reindex(df.index).ffill().values
    gap_idx = None
    for i in range(max(1, peak_idx - 20), min(peak_idx, T) + 1):
        if (o[i] / c[i - 1] - so[i] / scx[i - 1]) > 0.02 and c[i] > c[i - 1]:
            gap_idx = i
            break
    if gap_idx is not None and gap_idx >= 11:
        rej = None
        for i in range(gap_idx - 1, max(0, gap_idx - 10) - 1, -1):
            if h[i + 1] < h[i]:          # 次日未创新高 = 受阻确认（无容差）
                rej = i
                break
        if rej is not None:
            level = float(h[rej])
            bx = None
            for i in range(rej + 1, T + 1):
                if c[i] > level and c[i - 1] <= level:
                    bx = i
                    break
            if bx is not None:
                broken = bool((c[bx + 1:T + 1] < level).any())
                candidates.append((level, bx,
                                   f"最后受阻高点({df.index[rej].date()}高{level:.2f}，"
                                   f"{df.index[rej + 1].date()}未创新高)", broken))
    # 回退候选：近 90 日被突破、未收盘破回的前分形高点
    cands = pivot_highs[(pivot_highs < peak_idx - k) & (pivot_highs >= peak_idx - 90)]
    for p in cands[::-1]:
        level = float(h[p])
        cross = None
        for i in range(p + k + 1, T + 1):
            if c[i] > level and c[i - 1] <= level:
                cross = i
                break
        if cross is None or T - cross > cfg.sr_flip_max_age:
            continue
        broken = bool((c[cross:T + 1] < level).any())
        candidates.append((level, cross, "前swing高", broken))
        break

    if not candidates:
        return EdgeResult("sr_flip", 10.0, cfg.state(10.0),
                          ["近窗口内未见'前阻力被有效突破'的水平结构"], None)
    valid = [cd for cd in candidates if not cd[3]]
    if not valid:
        return EdgeResult("sr_flip", 15.0, cfg.state(15.0),
                          [f"水平结构({cd[2]}) {cd[0]:.2f} 已被收盘跌破，setup 失效（H6）"
                           for cd in candidates], None)
    level, bx, kind, _ = min(valid, key=lambda x: abs(x[0] - c[T]))
    dist = (c[T] - level) / atrv
    base = 70.0 if abs(dist) <= 1.0 else (45.0 if abs(dist) <= 2.0 else (25.0 if abs(dist) <= 3.0 else 10.0))
    ev.append(f"水平结构({kind})，突破日 {df.index[bx].date()}，现价距其 {dist:+.2f}×ATR")
    ev.append("突破后未收盘跌破该位（自突破日起逐日收盘核验）")
    if "Volume" in df.columns:
        base_v = df["Volume"].values[max(0, bx - cfg.vol_baseline_days):bx].mean()
        if base_v > 0 and df["Volume"].values[bx] / base_v >= 1.5:
            base += 10
            ev.append(f"突破日量能 {df['Volume'].values[bx] / base_v:.1f} 倍于基线")
    score = _clamp(base)
    return EdgeResult("sr_flip", round(score, 1), cfg.state(score), ev, level=float(level))


# ---------------------------------------------------------------- 趋势线（跳变锚定 HH 结构线，视频 37:45/43:30 口径）
def trendline_edge(df, spx, cfg: Config, atrv: float, pivot_highs: np.ndarray,
                   peak_idx: int) -> EdgeResult:
    """META 趋势线 = **一条固定的几何对象**：基底顶部 higher-highs 结构线。

    选线（无百分比容差、无刀刃窗口；锚点由价格结构自身证明）：
    1. 收集 swing-high 分形点（窗口从 `trendline_lookback` 起步，**自动向左扩展**
       直到能核验锚点或达到 `trendline_lookback_max`）；
    2. **结构锚点** = 最近一个同时满足 ①创近 `trendline_anchor_r`(4) 个 swing high
       新高、②前一 pivot 未创新高（fresh 顶部延续不算结构突破）、③对陈旧顶部
       决定性收复 ≥ `trendline_anchor_reclaim`(1%) 的 pivot（它终结 lower-highs
       序列、开启本段 HH 链）。Google：5/22 唯一合格——6/10 的前驱仍是 fresh 顶部
       （②出局），6/30 仅 +0.07% 非决定性（③出局）。选线由结构证明，与"窗口
       是否刚好包进某点"或阈值微调无关（R∈{3,4,5}、δ∈{0.5%–3%} 平台稳定）；
       锚点距本段动能腿 > `trendline_leg_window`(120) 根K线（跨段旧线）→ 低可信降级；
    3. **HH 链** = 从锚点向动能峰 forward 取"高点 ≥ 前一保留点"的序列
       （lower-high 回撤点按 C6b 剔除：6/17 177.36 < 6/10 181.11，出局）；
    4. 线 = HH 链首点—末点的固定点对连线（5/22—6/30）；
    5. **突破日** = 收盘首次上穿该线的日子（7/17），随后整段回踩期冻结：
       x 轴 = **交易日索引**（每根 K 线一个单位，与日线图画线一致），
       线值按固定斜率逐交易日推进，不随收盘价重选。

    Google 案例：线 5/22—6/30，斜率 +0.178/根K线，突破日 7/17。
    线值：7/28=184.62 → 7/31=185.15 → **8/1=185.33** → 8/4=185.51 → 8/5=185.69。
    与 20MA(186.18)/7-21 低点(186.15) 同带，即视频 [45:12]「跌破 7/21 K 线 = 同时
    跌破 20MA、支撑区、这条趋势线」的三线同带结构。

    稳健性：初始窗口 20–200 经自动扩展后锚点核验一致 → 选线与 8/1 线值恒定；
    网格敏感性 R∈{3,4,5}×δ∈{0.5%,1%,2%,3%} 中任何逃逸组合（如 R=5,δ=2% 会触及
    2024 旧线）都被 leg_window 守卫拦截为"低可信"降级；锚点无法核验时证据如实
    标注且分数封顶 Emerging——宁可降级也不静默换线。
    """
    T = len(df) - 1
    c = df["Close"].values
    k = cfg.pivot_k
    highs = df["High"].values

    def collect(lb):
        lo = max(k + 1, peak_idx - lb)
        return [int(p) for p in pivot_highs[(pivot_highs >= lo) & (pivot_highs < peak_idx)]]

    R, delta = cfg.trendline_anchor_r, cfg.trendline_anchor_reclaim

    def is_anchor(i):
        w = pivs[max(0, i - R + 1):i + 1]
        if highs[pivs[i]] != max(highs[w]):
            return False                      # (1) 须创近 R 个 swing high 新高
        pw = pivs[max(0, i - R):i]
        prev_max = max(highs[pw]) if pw else None
        if prev_max is not None and highs[pivs[i - 1]] == prev_max:
            return False                      # (2) 前一 pivot 仍是顶部(fresh)→非结构突破
        if prev_max is not None and highs[pivs[i]] < (1 + delta) * prev_max:
            return False                      # (3) 须决定性收复陈旧顶部
        return True

    lb = cfg.trendline_lookback
    pivs, anchor_idx = [], None
    while True:
        pivs = collect(lb)
        anchor_idx = next((i for i in range(len(pivs) - 1, 0, -1) if is_anchor(i)), None)
        if anchor_idx is not None or lb >= cfg.trendline_lookback_max or lb >= peak_idx - k - 1:
            break
        lb += 20
    if len(pivs) < 2:
        return EdgeResult("trendline", 10.0, cfg.state(10.0),
                          ["可用范围内 swing high 不足，无法构造 HH 结构线"], None)
    truncated = anchor_idx is None
    if not truncated and T - pivs[anchor_idx] > cfg.trendline_leg_window:
        truncated = True   # 锚点跨段（距本段动能腿过远），低可信
    start_p = pivs[anchor_idx] if not truncated else pivs[0]
    chain = []
    for p in pivs[pivs.index(start_p):]:
        if not chain or highs[p] >= highs[chain[-1]]:
            chain.append(p)
    if len(chain) < 2:
        return EdgeResult("trendline", 15.0, cfg.state(15.0),
                          ["低可信：锚点之后不存在 higher-highs 链（<2 点），"
                           "该锚点不能定义结构线（如 δ=0 时锚点退化为末点本身）"], None)
    p1, p2 = chain[0], chain[-1]
    slope = (highs[p2] - highs[p1]) / (p2 - p1)   # 每根K线的价格变化（交易日索引系）
    b = highs[p1] - slope * p1
    cross = None
    for i in range(p2 + 1, T + 1):
        if c[i] > slope * i + b and c[i - 1] <= slope * (i - 1) + b:
            cross = i
            break
    vT = slope * T + b
    ev_prefix = []
    if anchor_idx is None:
        ev_prefix.append(f"窗口扩展至 {lb} 交易日仍无合格结构锚点（近{cfg.trendline_anchor_r}点新高+"
                         f"决定性收复≥{cfg.trendline_anchor_reclaim:.0%}）——HH 链起点无法核验（窗口截断），结构线低可信")
    elif truncated:
        ev_prefix.append(f"锚点 {df.index[start_p].date()} 距本段动能腿超过 {cfg.trendline_leg_window} 根K线"
                         "（跨段旧线），不能作为本段阻力线——结构线低可信")
    if cross is None or T - cross > 45:
        return EdgeResult("trendline", 15.0, cfg.state(15.0),
                          ev_prefix + [f"HH 结构线（{df.index[p1].date()}—{df.index[p2].date()}）近45日未见收盘上穿，与本段动能无关"],
                          level=float(vT),
                          meta={"p1": str(df.index[p1].date()), "p2": str(df.index[p2].date()),
                                "slope_per_bar": round(float(slope), 4), "cross": None})
    gap = (c[T] - vT) / atrv
    base = 70.0 if abs(gap) <= 1.0 else (45.0 if abs(gap) <= 2.0 else (25.0 if abs(gap) <= 3.0 else 10.0))
    ev = ev_prefix + [
        f"HH 结构线（冻结）{df.index[p1].date()}({highs[p1]:.2f})—{df.index[p2].date()}({highs[p2]:.2f})，"
        f"锚点 {df.index[start_p].date()}（近{cfg.trendline_anchor_r}点新高且决定性收复陈旧顶部），"
        f"HH 链 {len(chain)} 点（lower-high 已剔除），突破日 {df.index[cross].date()}",
        f"线值@T={vT:.2f}（现价 {c[T]:.2f}，{gap:+.2f}×ATR）；x 轴=交易日索引，"
        f"斜率 {slope:+.3f}/根K线；同一条线贯穿回踩期，不随收盘价重选"]
    if len(chain) >= 3:
        base += 10
        ev.append("HH 链≥3 点，顶部阻力多次确认")
    if "Volume" in df.columns:
        base_v = df["Volume"].values[max(0, cross - cfg.vol_baseline_days):cross].mean()
        if base_v > 0 and df["Volume"].values[cross] / base_v >= 1.5:
            base += 10
            ev.append(f"突破日量能 {df['Volume'].values[cross] / base_v:.1f} 倍于基线")
    if truncated:
        base = min(base, 55.0)
    score = _clamp(base)
    return EdgeResult("trendline", round(score, 1), cfg.state(score), ev, level=float(vT),
                      meta={"p1": str(df.index[p1].date()), "p2": str(df.index[p2].date()),
                            "slope_per_bar": round(float(slope), 4),
                            "cross": str(df.index[cross].date()),
                            "anchor": str(df.index[start_p].date()), "expanded_window": lb})


# ---------------------------------------------------------------- 相对强度（C6a/H4）
def rs_edge(df, spx, cfg: Config) -> EdgeResult:
    T = len(df) - 1
    c = df["Close"].values
    sc = spx["Close"].reindex(df.index).ffill().values

    def rel(n):
        if T < n:
            return None
        return np.log(c[T] / c[T - n]) - np.log(sc[T] / sc[T - n])

    ev = []
    parts = []
    for n, wt in ((21, 0.6), (63, 0.4)):
        r = rel(n)
        if r is None:
            continue
        s = _clamp(25 + r * 100 * 7.5)   # 超额+10%→100，0→25，-3%→2.5
        parts.append((s, wt))
        ev.append(f"近{n}日相对SPX超额 {r * 100:+.1f}%")
    score = sum(s * wt for s, wt in parts) if parts else 25.0

    # 大盘下跌日表现（H4: beta>1 的股跌得比大盘少 = 强 RS）
    spx_ret = pd.Series(sc, index=df.index).pct_change()
    stk_ret = pd.Series(c, index=df.index).pct_change()
    down = spx_ret[(spx_ret < -0.005)].iloc[-10:]
    if len(down):
        m = (stk_ret[down.index] - down).mean()
        score += 25 if m >= 0.01 else (15 if m >= 0 else 0)
        ev.append(f"近{len(down)}个SPX下跌日（<-0.5%）个股日均超额 {m * 100:+.2f}%")

    # RS 线站上其 20MA（"RS 线重新出现"）
    ratio = rs_ratio(df["Close"], pd.Series(sc, index=df.index))
    rma = ratio.rolling(20).mean()
    if ratio.iloc[-1] > rma.iloc[-1]:
        score += 10
        ev.append("RS线（个股/SPX）位于其20MA上方")
    # 超跌反弹的伪强势修正：绝对动能为负（近21日下跌）时，横盘抗跌不得视为 RS 成熟
    abs_ret21 = np.log(c[T] / c[T - 21]) if T >= 21 else 0.0
    if abs_ret21 < 0 and score >= cfg.mature_threshold:
        score = cfg.mature_threshold - 5
        ev.append(f"近21日绝对收益 {abs_ret21 * 100:.1f}% 为负：仅为超跌横盘，非真 RS（视频 H3：RS 须伴随上涨）")
    score = _clamp(score)
    return EdgeResult("rs", round(score, 1), cfg.state(score), ev)


# ---------------------------------------------------------------- 市场结构（C6b）
def market_structure_edge(df, spx, cfg: Config, pivot_highs: np.ndarray) -> EdgeResult:
    T = len(df) - 1
    c = df["Close"].values
    h = df["High"].values
    recent = pivot_highs[pivot_highs >= T - 60]
    hh = 0
    for a, b in zip(recent[:-1], recent[1:]):
        if h[b] > h[a]:
            hh += 1
    score = 25 + 15 * min(hh, 3)
    ev = [f"近60日 swing high 中 {hh} 个 higher high"]
    hi60 = max(h[max(0, T - 60):T + 1])
    if c[T] >= 0.95 * hi60:
        score += 20
        ev.append(f"现价处于60日高位区（距60日高点 {(c[T] / hi60 - 1) * 100:+.1f}%）")
    lo20 = min(df["Low"].values[max(0, T - 19):T + 1])
    if c[T] <= lo20 * 1.01:
        score -= 30
        ev.append("收盘价接近20日低点（结构弱化）")
    score = _clamp(score)
    return EdgeResult("market_structure", round(score, 1), cfg.state(score), ev)
