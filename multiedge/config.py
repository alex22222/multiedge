"""参数集中定义：改参数=改规则。所有阈值对应视频观点，见 PRD/ARCHITECTURE。"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Config:
    # 指标
    ma_periods: tuple = (10, 20, 50)
    atr_period: int = 14
    pivot_k: int = 3            # 分形 pivot 臂长
    # 动能（观点 B4/B5/H3：不到一个月 ~14% 对超大市值=极强）
    momentum_window: int = 21
    momentum_lookback: int = 42
    momentum_strong_pct: float = 0.14
    # 新鲜度（观点 B4）：动能峰距今 >5/>10/>15 日 → ×0.7/×0.45/×0.25
    fresh_full: int = 5
    fresh_mid: int = 10
    fresh_old: int = 15
    # 成熟度
    mature_threshold: int = 60
    emerging_threshold: int = 30
    # MEA 汇合（观点 B13/A1：多结构同价位带；均线是区间）
    mea_atr_tolerance: float = 1.0     # 现价到 MEA 汇合区中心的最大距离
    mea_cluster_atr: float = 2.0       # 结构位聚类的最大跨度
    # 首回调（观点 B2）：动能峰后多久内的回踩算"第一次回调"
    first_pullback_max_days: int = 10
    # 周线 regime（观点 G1/G2/H1/H3：高时间框架支配低时间框架）
    weekly_regime_weeks: int = 30
    # S/R 翻转与趋势线有效期
    sr_flip_max_age: int = 30
    trendline_lookback: int = 50   # HH 结构线最小窗口（交易日）；不足以下窗口会自动扩展直到可核验突破跳变锚点
    trendline_lookback_max: int = 260  # 扩展上限（防数据不足时死循环）
    trendline_anchor_r: int = 4      # 锚点'近R个swing high新高'窗口
    trendline_anchor_reclaim: float = 0.01  # 锚点对陈旧顶部须决定性收复≥1%（δ 平台 0.5%–3%）
    trendline_leg_window: int = 120  # 锚点须在本段动能窗内（防跨段旧线），超出→低可信降级
    ma200_period: int = 200        # 200 日均线（视频 H1），机检输出用
    # 催化剂（观点 B5/C1）：个股相对大盘的显著跳空
    catalyst_rel_gap: float = 0.025
    # 量能结构（观点 B6/D2）
    vol_baseline_days: int = 60
    # 否定条件（观点 J7/B12 反面）
    mkt_crash_day: float = -0.015
    mkt_crash_3d: float = -0.025
    # 止损缓冲：META 结构低点下方 0.10×ATR（E1/E2）
    stop_buffer_atr: float = 0.10

    def state(self, score: float) -> str:
        if score >= self.mature_threshold:
            return "Mature"
        if score >= self.emerging_threshold:
            return "Emerging"
        return "Absent"
