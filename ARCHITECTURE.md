# ARCHITECTURE.md — 多趋势验证买入系统架构

## 总览

```
ticker + as_of ──▶ data.py ──▶ indicators.py ──▶ edges.py ──▶ engine.py ──▶ 报告(文本/JSON)
                      │               │                │            │
              MarketWatch CSV     MA/EMA/ATR/      7 条边缘      MEA 多结构汇合
              缓存 data/*.csv     pivot/30周SMA    打分 0-100    判定/止损/风险
```

纯 Python（≥3.10），依赖仅 `pandas`、`numpy`、`pytest`（见 `requirements.txt`）。无状态——给定同一数据快照输出唯一。

## 模块

### `multiedge/config.py`
全部参数集中定义：均线 `[10,20,50]`、ATR(14)、pivot 臂长 `k=3`、动量窗 `21d`/`lookback 42d`、新鲜度折扣（峰龄 >5/>10/>15 日 ×0.7/0.45/0.25）、MEA 容差 `mea_atr_tolerance=1.0×ATR` 与聚类跨度 `mea_cluster_atr=2.0×ATR`、首回调窗 `first_pullback_max_days=10`、周线 regime `30 周`、催化跳空 `catalyst_rel_gap=2.5%`、趋势线最小窗口 `trendline_lookback=50`（自动扩展至锚点可核验，上限 260）、锚点规则 `trendline_anchor_r=4` + `trendline_anchor_reclaim=1%` + 跨段守卫 `trendline_leg_window=120`、止损缓冲 `stop_buffer_atr=0.10`。非视频来源的参数与规则已全部列入 PRD「操作化假设清单」（取值/理由/视频依据/已测敏感性范围）；改参数=改规则。

### `multiedge/data.py`
- `fetch_daily(symbol, start, end, cache_dir)`：MarketWatch 历史 CSV（Dow Jones，`downloaddatapartial` 端点，无需 API key；Yahoo/Stooq 在本环境被限流，见 ITERATION_LOG 第 0 轮）。个股 `/investing/stock/`、指数 `/investing/index/`。日线**未复权**口径（GOOGL 无分红、SPX 为指数，无影响）。
- 缓存 `data/<symbol>_<start>_<end>.csv`，命中即读——保证可复现。

### `multiedge/indicators.py`
- `sma/ema/atr`（Wilder ATR14）
- `swing_pivots(df, k)`：分形 pivot 高/低点
- `weekly_sma(close, weeks)`：周五重采样 → rolling → 前向填充（高时间框架 regime）
- `momentum_peak(close, T, window, lookback)`：**统一的动能峰定义**——近 lookback 日内最佳 window 日对数收益窗的终点索引。engine 与 momentum_edge 共用，避免两处不一致。
- `rs_ratio`：个股/SPX 价格比（RS 线）

### `multiedge/edges.py` — 每条边缘 = `{key, score(0-100), state, evidence[], level}`
| 边缘 key | 打分逻辑（要点） |
|---|---|
| `momentum` | 统一峰（见上）的 21 日对数涨幅相对阈值缩放（14%/月=100 分）；催化跳空高开（>2%，**不要求收阳**）+10；量能确认加分；新鲜度折扣（B4/B5/H3） |
| `volume_pattern` | 峰前 10 日内 climax 单日量 ÷ 回调末 3 日均量，55×log2 映射；climax≥1.5×基线 +20；多日放量 +10（B6/D2） |
| `ma_zone` | **20EMA 与 20SMA 两线构成区间**（B13/H8），现价到区间距离打分；近 3 日测试支撑加分；跌破 50MA 扣分 |
| `sr_flip` | **前阻力翻支撑**（H5⑤）：催化 gap 前 [gap−10, gap) 窗口内，**最后确认受阻的高点**（h[i+1] < h[i]，次日未创新高即受阻确认——无容差参数），被向上突破且突破后未收盘跌破（逐日核验）。后备：近 90 日被突破的前 swing 高。与 E1 止损锚是两个独立对象。8/1 = **184.33**（7/16 高，7/17 未创新高确认受阻，7/18 突破），距 20MA 0.43×ATR |
| `trendline` | **冻结的基底顶部线**（见下） |
| `rs` | 21/63 日超额收益加权 + 大盘下跌日超额 + RS 线上穿其 20MA；**近 21 日绝对收益为负时不得因横盘抗跌给 Mature**（H3/H4） |
| `market_structure` | 60 日 higher-high 计数 + 高位区加分 / 20 日新低扣分（C6b，辅助边缘，**不得顶替趋势线/水平结构**） |

### 趋势线：结构锚定 HH 结构线（视频 37:45/43:30 口径，无容差、无刀刃窗口）
1. 从 `trendline_lookback`（50 交易日）起步收集 swing-high 分形点，**窗口自动向左扩展**
   直到能核验锚点（或达 `trendline_lookback_max`=260）——窗口只是候选池上界，不参与选线；
2. **结构锚点** = 最近一个同时满足 ①创近 `trendline_anchor_r`(4) 个 swing high 新高、
   ②前一 pivot 未创新高（fresh 顶部延续不算结构突破）、③对陈旧顶部决定性收复
   ≥ `trendline_anchor_reclaim`(1%) 的 pivot（终结 lower-highs、开启本段 HH 链）。
   Google：5/22 唯一合格（6/10 前驱仍 fresh 出局；6/30 仅 +0.07% 非决定性出局）；
3. **HH 链** = 锚点向动能峰 forward 取"高点 ≥ 前一保留点"的序列（lower-high 按 C6b 剔除：
   6/17 177.36 < 6/10 181.11，出局）→ {5/22, 6/10, 6/30}；
   锚点距本段动能腿 > `trendline_leg_window`(120) 根K线（跨段旧线）→ 低可信降级；
4. 线 = HH 链**首点—末点**固定点对连线（5/22—6/30）；
5. **突破日** = 收盘首次上穿（7/17），随后整段回踩期冻结：x 轴 = **交易日索引**
   （每根 K 线一个单位，与日线图画线一致；非自然日），线值按固定斜率逐交易日推进，
   不随收盘价重选。报告输出冻结点对、每根 K 线斜率、突破日、线值（`trendline_frozen`）；
6. 锚点无法核验或跨段 → 分数封顶 55 并标注"低可信"，宁可降级也不静默换线。

Google 案例：线 5/22—6/30，斜率 **+0.178/根K线**，突破日 7/17。线值：7/28=184.62、7/31=185.15、**8/1=185.33**、8/4=185.51、8/5=185.69（恒定对象）。这就是视频 [45:12] 止损逻辑里的"这条趋势线"：跌破 7/21 K 线 = 同时跌破 20MA、支撑区与这条线（三线同带）。敏感性测试：初始窗口 {20,34,41,42,61,80,100,150,200} 经自动扩展后选线与线值完全一致；锚点网格 R∈{3,4,5}×δ∈{0.5%,1%,2%,3%} 共 12 组合一致。

### `multiedge/engine.py`
1. 统一动能峰 `momentum_peak`；`pullback_age = T − peak`；
2. 计算 7 条边缘；
3. **MEA 汇合区**：成熟结构边缘（ma_zone/sr_flip/trendline）的 level 聚类——跨度 ≤`mea_cluster_atr`×ATR 且现价到带中心 ≤`mea_atr_tolerance`×ATR（**多结构同价位带，单均线带不算**）；
4. **催化剂检查**（B5/C1）：动能窗 `[peak−20, peak]` 内存在相对大盘跳空 ≥`catalyst_rel_gap`（2.5%）或单日相对涨幅 ≥8%；
5. **判定（全 AND 才 BUY，见 PRD §3）**：动能 Mature 且新鲜；RS Mature；量能结构 Mature；≥2 结构边缘成熟且成带；`pullback_age ≤ first_pullback_max_days`；周线 regime（收盘 ≥30 周 SMA）；无否定条件；
6. **否定条件**：20 日新低；大盘急跌（当日 ≤−1.5% 或近 3 日 ≤−2.5%）且个股破 50MA；回调末段量超 climax 量；收盘 <30 周 SMA；
7. **E1 止损参考锚**（独立输出 `stop_anchor`）：催化 gap 日前 leg 内最大成交量阳线的低点（视频"以 7/21 这根K线为退出参考"）——与 sr_flip 分开实现、分开输出；
8. **止损** = 近端结构带（20MA 区间 / 冻结趋势线 / E1 锚）下沿 −`stop_buffer_atr`(0.1)×ATR（跌破即多结构同破，对应视频 7/21 K 线下方口径）；**目标**：peak 前 21~180 日最高 pivot 高；输出 RR；
9. **200DMA 输出**（信息项）：`ma200` 与 `dist_200ma_pct`（视频 H1 的机检证据，不进 BUY 判定——6/20 收在其下、8/1 站上，测试锁定）。

### `multiedge/cli.py`
```
.venv/bin/python -m multiedge.cli GOOGL --as-of 2025-08-01        # 单日判定（文本+JSON）
.venv/bin/python -m multiedge.cli GOOGL --as-of 2025-08-08 --scan 2025-04-01 2025-08-08
.venv/bin/python -m pytest tests/ -q
```

## 数据流（以 GOOGL @ 2025-08-01 为例）

1. 读缓存 GOOGL/SPX 日线（MarketWatch，2024-08-08→2025-08-08）；
2. `momentum_peak` 定峰 = 7/24（+15.2%/21 日，含 +3.6% 催化跳空）；
3. 边缘：momentum 85、volume_pattern 71（climax 74.9M÷回调末 39.5M=1.89）、ma_zone 90（区间 [186.18,187.21]）、sr_flip 45（**最后受阻高点 184.33**，同带稳定 Emerging；现价距其 1.14×ATR）、trendline 80（HH 结构线 5/22—6/30 @185.33，结构锚点 5/22，突破日 7/17，斜率 +0.178/根K线）、rs 91、market_structure 90；
4. 结构 level {185.33, 186.70}（成熟者）聚类（跨度 1.37×ATR），现价距带中心 0.72×ATR → **MEA [185.33, 186.70]**（184.33 为带内更深一层支撑）；催化 ✓；首回调 6 日 ✓；regime ✓ → **BUY**；
5. E1 锚 = 7/21 放量K线低点 186.15（独立输出）；止损 = 近端带下沿 185.33 − 0.43 = 184.91（−2.2%），目标 207.05，**RR 4.25R**；200DMA = 175.03（收盘在其上 +8.1%，信息项）。

### `multiedge/report.py` — 自包含 HTML 报告（第 9 轮）
- `render_report(ticker, as_of)` → `(html, Report)`：调用 `engine.analyze(..., with_chart=True)`，**页面上所有数字只来自 analyze() 输出**（单一事实来源），模板/JS 不重算任何指标或判定（页面无 JS，仅内联 SVG+CSS）。
- 区块：结论卡 → 多趋势验证清单（来自 `Report.checklist`，每条含实际值/阈值/视频观点编号）→ 价格图（`Report.chart`：K线、MA10/20/50/200、冻结 HH 结构线、水平结构位、MEA 带、止损/目标线、动能峰/催化跳空/突破日标记）→ 边缘成熟度条+证据 → 风险/出场参考 → GOOGL 8/1 自动附加「视频对照区」（视频参考值硬编码于 `GOOGLE_VIDEO_REF`，标注为视频口径）→ 操作化假设折叠区（逐项列 Config 字段，指向 PRD 清单）。
- 生成：`python -m multiedge.report GOOGL --as-of 2025-08-01 -o reports/GOOGL_2025-08-01.html`；`reports/` 预生成 GOOGL 8/1（BUY）、GOOGL 6/30（NO）、NVDA 11/14（止损不可用）。

### 图表数据（`Report.chart`，`with_chart=True`，第 10 轮扩展）
- 130 根窗口的 OHLCV、`ma10/20/50/200`（`data.fetch_daily_chunked` 分块取长历史，保证 200MA 全程无 None）、冻结 HH 结构线（**仅从锚点起**，锚点/链上 pivot 打点）、`sr_level`、`mea_zone`、`stop/target`、`climax_pos`（峰前 10 日最大量）与 `pullback_last3`（回调末 3 日）的窗口内位置、四个事件标记（动能峰/催化跳空/突破日/E1 锚）。
- 渲染（`report.render_chart`，内联 SVG，无 JS）：价格主图 + 量能副图（climax 青框、回调末 3 日半透明）；标注用编号 ①②③ + 底部图例（同日标记自动水平错开），HH 线标签在线右端，避免压字/裁切；y 轴量程含全部均线序列，MA200 不再"中段才开始"。

### 图表标签系统（第 11 轮）
- 右侧 150px **标签槽**：水平线画到 W−150，标签（目标/MEA 带/水平结构/止损/HH 结构线）统一进槽，按 y 排序、行距 <13px 自动错行，不再压 K 线；「MEA 带」标签贴在带的 y 处而非图顶。
- 事件标记 ①②③ + 底部图例；同日多标记水平自动错开。锚点在窗口外时标注「链点 MM-DD（锚点 MM-DD 在图左外侧）」，不把首个链点误标为锚。
- SVG 文本逐元素 `font-family="'Noto Sans CJK SC','Microsoft YaHei',sans-serif"`（cairosvg 不做字体栈回退，首个族必须存在；浏览器正常回退）。

### `multiedge/app.py` — 本地交互（加分项；第 12 轮起默认 start 与 report 同为 LONG_START）
- 标准库 `http.server`，**只监听 127.0.0.1**（`python -m multiedge.app [--port 8321]`），`/` 为输入表单，`/report?ticker=&as_of=` 渲染与静态报告完全相同的页面；异常如实返回 500 页。

## 测试（tests/，52+ 用例）

- `test_indicators.py`：ATR/MA/pivot/RS/momentum_peak 数值正确性；
- `test_google_case.py`：
  - 8/1：RS/趋势线/均线/量能 Mature + 水平结构同带稳定 Emerging；**趋势线/水平结构价位距 20MA(186.18) ≤0.75×ATR 的绝对断言**（非 min–max 自指）；水平结构 = 184.33 最后受阻高点且**与 E1 止损锚（186.15）非别名**；止损 1.6–2.6%；RR 3.5–5.5；
  - **趋势线冻结性**：7/28→8/5 线值稳定在 180–189、相邻日仅斜率级推进、冻结点对=5/22—6/30；
  - **lookback 敏感性**：45/50/53 交易日窗口选线与 8/1 线值不变（plateau）；
  - **200DMA**：6/20 收在其下、8/1 明确站上；
  - **44 个负例日**（5 月杂讯、6 月全月、7/7–7/10、7/28、8/4、8/5）不得 BUY；
  - 可复现性（两次运行输出一致）。

## 已知取舍

- 趋势线是"跳变锚定 HH 结构线"，是视频 [37:45/43:30] 画线口径的机检代理；不同数据源 pivot 的微小差异可能影响 HH 链端点。
- 催化剂以相对跳空/单日相对剧涨机检代理（B5），不解析公告文本。
- 周线 regime 用 30 周 SMA（G1/G2 的高时间框架过滤），**它不是视频的 200 日均线**；本案例 6 月收盘也在其上方，区分度由催化剂门槛与 RS 修正提供（PRD 已如实写明）。
