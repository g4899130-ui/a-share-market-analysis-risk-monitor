# 基于 Python 的 A 股行情数据分析与风险监测系统

这个项目保留原来的 20 只 A 股日线采集、最新行情、SQLite 存储、去重增量更新、轮询和 CSV 导出，并新增历史收益、波动率、最大回撤、相关性与演示用风险提示。面向金融数据分析、金融科技和风险管理实习的学习与展示；结果不构成投资建议。

## 技术栈和安装

Python 3.10+；`requests` 请求东方财富公开网页接口，`pandas` 清洗和分析，标准库 `sqlite3` 保存数据，`matplotlib` 保存 PNG。安装：

```powershell
python -m pip install -r requirements.txt
```

## 运行命令

在项目目录执行：

```powershell
python main.py status
python main.py update
python main.py quotes
python main.py export
python main.py backfill
python main.py analyze
python main.py analyze --start 2026-01-01 --end 2026-10-16 --codes 600519 000001
python main.py watch --interval 60
python -m unittest discover -s tests -v
```

`update` 更新历史日线和最新行情；`quotes` 只刷新快照；`backfill` 只补历史日线，适合快照接口暂不可用时使用。历史抓取默认从运行当天往前 400 个自然日开始，可用 `--start YYYY-MM-DD` 指定更早日期。`watch` 每轮最少 30 秒，按 Ctrl+C 停止。原有四张数据库表保持不变，日线以 `(code, trade_date)` 为主键，重复更新执行 UPSERT，不会生成重复日线。`export` 将三张业务表输出为 `data/*.csv`。`analyze` 只读 SQLite，生成 `results/` 下的文件，不访问网络。

## 结果文件

| 文件 | 内容 |
| --- | --- |
| `data/stock.db` | 原始 SQLite 数据库，含 `stocks`、`daily_bars`、`latest_quotes`、`update_runs` |
| `results/risk_return_summary.csv` | 每股累计收益率、日收益率标准差、年化波动率、最大回撤、样本数和风险提示 |
| `results/daily_returns.csv` | 按日期对齐的日收益率，真实缺失保留为空 |
| `results/return_correlation.csv` | Pearson 相关系数矩阵 |
| `results/correlation_overlap_days.csv` | 每对股票同日有效收益率样本数 |
| `results/close_prices.png` | 历史收盘价走势（元；股价水平不代表收益表现） |
| `results/cumulative_returns.png` | 共同起点的累计收益率对比（%） |
| `results/drawdowns.png` | 各股票历史回撤曲线（%） |
| `results/correlation_heatmap.png` | 收益率相关性热力图 |
| `results/analysis_report.md` | 当前结果的口径和样本限制 |

## 金融指标口径

`daily_bars.close` 来自原项目的 **不复权** 日线，全部指标使用同一口径，未混入最新行情快照。日期升序处理。日收益率是 `当日收盘价/前一个对齐交易日收盘价-1`；若在其他股票的交易日期上该股票缺失价格，则不跨越缺口算收益。每只股票首日收益率为空，不用 0 填。累计收益率是 `期末收盘价/共同起始日收盘价-1`，在所有参与比较的股票都有价格的首日设为 0%。所有股票在共同起止日期比较，避免不同起点直接相比。

日波动率是有效日收益率的**样本标准差**（`ddof=1`）；年化历史波动率为 `日波动率×√252`，假设一年 252 个交易日。它描述历史收益率的离散程度，并非未来风险预测。净值为 `收盘价/区间首日收盘价`；每日回撤为 `净值/截至当日最高净值-1`，最大回撤取每日回撤的最小值，保留负号。相关性用两股**相同交易日期**都有效的日收益率逐对计算 Pearson 系数；不足两个配对样本时为空。

`config.py` 集中设置演示阈值：年化波动率 >40% 提示高波动，最大回撤绝对值 >20% 提示较大回撤；少于 60 个有效日收益率会标记样本不足，少于 200 个提示短于约一年。绝对日收益率 >20% 会标记待核查，不擅自删除或替换。停牌、缺失数据导致的非同步日期会在收益率矩阵保留空值，并统计缺口。

## 数据范围与已知局限

原始附件数据库仅有 2026-09-01 至 2026-09-24，每股 18 个收盘价。本次已通过原接口回补到 2025-09-11 至 2026-10-16，每股 262 个收盘价，合计 5240 条日线；之后又运行 `update` 验证原功能，20 只股票全部成功，数据库仍为 5240 条。交付结果以本次真实回补数据计算。以后可用 `python main.py backfill` 扩展历史范围，再运行 `analyze`。若外部接口访问失败，程序保留已有真实数据，不能把旧数据库快照称为最新行情。

不复权价格在分红、送转、拆股等事件附近可能产生机械跳变，因此价格收益率不等于含股息的投资者总回报，也可能扭曲波动率、回撤与相关性。公开网页接口可能限流、延迟或变更；`latest_quotes.quote_time` 与本地 `fetched_at` 要区分。原项目使用 HTTP 接口，数据传输不用于敏感信息或交易决策。这里只做历史描述和透明阈值提示，没有 AI 预测。

## 代码入口

- `main.py`：原采集、SQLite、更新、导出和命令入口；新增 `backfill` 与 `analyze`。
- `analysis.py`：数据校验、收益率、波动率、回撤、相关性、风险汇总。
- `visualization.py`：四张 PNG 图。
- `config.py`：演示风险阈值。
- `tests/test_analysis.py`：人工可核算的模拟数据单元测试。
- `项目代码学习指南.md`：逐文件学习路线和面试追问。
