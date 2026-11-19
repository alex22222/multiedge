# Paper历史保留与展示修复

记录日期：2026-11-19，Asia/Shanghai。

## 原因与修复

用户反馈上次交易看不到。服务器原始账本仍有两笔成交；页面默认选中独立空v2账户，旧记录只在账户下拉框中，未发生数据丢失，也未从备份重新导入成交。

Paper页顶部新增固定的“全部交易历史 · 跨版本保留”，默认显示全部前向账户已平仓交易，切换当前账户不隐藏历史。“查看原账户”打开对应规则、止损调整和每日盈亏。历史回放独立筛选，各版本仍独立记账，不相加账户收益率。

保存Paper时在同一SQLite写事务核对顶层与`state.trades`，已有平仓记录必须保持完整前缀；拒绝删除、改价、改损益或重排，允许追加新平仓及正常持仓/估值更新。策略版本、入场/移动政策与调度未变。

## 发布与账本核验

- 应用提交：`acedf4c253252ca1e690aa88f797ba333a36d897`。
- 活跃目录：`/opt/multiedge/releases/20261119T100839Z-acedf4c`。
- 目标仅173.199.122.23，未连接排除的polymtrade服务器。
- 一致性备份：`/var/lib/multiedge/archives/paper-history/before-20261119T100827Z.sqlite3`，SQLite完整性检查通过。
- 验收回执：`archives/paper-history/acceptance-20261119T100839Z.json`，路径相对`/var/lib/multiedge`。
- 全部6个账户身份保留；5个前向账户payload与备份逐字段相同。所有原平仓、现金、权益、持仓、订单、事件、曲线及政策未变；部署seed的回放账户仅`updated_at`改变，成交及其余字段相同。全部非Paper研究记录payload也未变。

原账户`forward-baseline-1719bb3932`：

| 标的 | 原买价 | 原卖价 | 净损益（美元） |
|---|---:|---:|---:|
| AMGN | 459.6597076721191 | 449.67504389953615 | -73.0753180385813 |
| MMM | 181.71080511474608 | 178.41075 | -59.16197016915861 |

旧账户仍有4条逐股每日盈亏，合计-132.23728821美元；不计入v2收益。v2账户仍为`forward-baseline-8c6b0fc42e`，未重建。

## 验证

- 修复前复现默认v2无跨版本入口；新增保存行为测试中6项先失败，修复后全部通过。本地及服务器完整测试均**356 passed**，JS语法及diff检查通过。
- 生产应用经SSH loopback浏览器核验：默认v2可见AMGN -73.08及MMM -59.16；按钮打开原账户2笔平仓和4条每日盈亏；切回v2、刷新后历史仍在。回放筛选只显示独立GOOGL样例。
- 桌面1600px、手机390px及手机横向滚动后原账户按钮通过，无整体横溢；控制台0错误/0警告。真实生产截图为本地忽略目录`output/playwright/paper-history-production-desktop.png`和`paper-history-production-mobile.png`，预览截图另命名。
- 应用、Caddy及两个timer active/enabled，应用`NRestarts=0`，无重启循环；健康200、原共享登录401、公网未登录303。

原`validation.json`归档为`archives/paper-history/validation-before.json`，当前验证说明更新356项及本轮历史验收；先前版本验收仍保留历史标签。备份和运行状态在发布目录外保留，升级不清空账户。
