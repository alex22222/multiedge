# Multi-Edge（多趋势验证买入系统）

从 J Law（Chart Fanatics）视频观点建模的研究/回看验证工具。输入一只股票和截止日期，系统会评估各观点与指标的成熟度，并给出是否出现多趋势验证买入信号的初步判定。

**这是研究工具，不构成投资建议，也不能作为下单依据。**

## 快速开始

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# 对一只股票跑判定
.venv/bin/python -m multiedge.cli GOOGL --as-of 2025-08-01

# 历史逐日扫描
.venv/bin/python -m multiedge.cli GOOGL --as-of 2025-08-08 --scan 2025-04-01 2025-08-08

# 测试
.venv/bin/python -m pytest tests/ -q
```

## 可视化报告

生成单个自包含 HTML（样式和图表全部内联，打开时不需要联网）：

```bash
.venv/bin/python -m multiedge.report GOOGL --as-of 2025-08-01 -o reports/GOOGL_2025-08-01.html
```

`reports/` 下预生成了三份示例：

- `GOOGL_2025-08-01.html`：视频中的 Google 案例，判定为 BUY
- `GOOGL_2025-06-30.html`：对照案例，判定为 NO
- `NVDA_2025-11-14.html`：止损/目标不可用时的示例，判定为 NO

用浏览器双击即可打开。

本地交互页面（输入股票代码和日期）只监听本机：

```bash
.venv/bin/python -m multiedge.app    # 默认 8321 端口
# 浏览器打开 http://127.0.0.1:8321/
```

页面上的数字都来自引擎输出，渲染层不会重新计算指标或判定。

## 文档

- `PRD.md`：需求、买入判定规则，以及视频之外的操作化假设
- `ARCHITECTURE.md`：模块、数据流、边缘打分
- `VIEWPOINTS.md`：视频观点清单（带时间戳）
- `ITERATION_LOG.md`：迭代记录

## 数据

默认使用 MarketWatch 历史 CSV（Dow Jones，未复权口径）。`data/` 中缓存了 GOOGL、NVDA 和标普 500（SPX）的示例区间，可直接复现上述报告和测试。没有缓存时会按需下载。

## 许可

未添加许可证。如需明确开源条款，请在仓库中补充 LICENSE。
