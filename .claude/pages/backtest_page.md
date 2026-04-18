# 回测页 BacktestPage

**文件：** `app/pages/backtest_page.py` (~2039 行)

## 页面定位

两态页面设计（QStackedWidget）：配置态（表单）→ 结果态（三栏布局）。支持对比模式：一键运行两种买入时机并对比绩效差异。

## 类结构

### _PercentAxisItem(pg.AxisItem)
Y 轴刻度以百分比显示的自定义坐标轴。

### BacktestWorker(QtCore.QObject)
后台回测任务 Worker，调用 `BacktestEngine.run()` + `calculate_metrics()`。

**信号：** `progressChanged(dict)`, `finished(dict)`, `errorOccurred(str)`
**方法：** `run()`, `cancel()`

### BacktestProgressDialog(QtWidgets.QDialog)
回测进度弹窗，展示两阶段进度：
- **precompute 阶段**：信号预计算（N只股票）
- **simulate 阶段**：时间步进（N个交易日），显示当前日期、总资产、今日交易数

### TradeDetailDialog(QtWidgets.QDialog)
交易详情弹窗，点击交易明细中的股票后弹出：
- 左侧：StockChartWidget 四联图 + B▲/S▼ 买卖标记
- 右侧：该股票的交易记录表（日期、方向、成交价、数量、金额）
- 点击交易记录行可跳转图表到对应日期

### BacktestPage(QtWidgets.QWidget)

**信号：** `statusMessageRequested = Signal(str, int)`
**构造参数：** `root: Path`

## 关键状态变量

| 变量 | 说明 |
|------|------|
| `template_service` | TemplateService 实例 |
| `_backtest_thread / _backtest_worker` | 回测任务线程与 Worker |
| `_progress_dialog` | 回测进度弹窗 |
| `_result` | BacktestResult 回测结果 |
| `_compare_result` | 对比模式的第二个结果 |

## 配置态 UI

- 策略选择：模板下拉 + 通达信条件代码编辑框
- 时间范围：开始日期 + 结束日期
- 资金参数：初始资金、单只仓位比例、最大持仓数
- 交易成本：佣金费率、最低佣金、印花税率
- 买入时机：信号日收盘价 / 次日开盘价
- 卖出策略：default（默认止损）/ brick_chart（砖形图超短线）
- 买入评分器：无 / brick（砖形图评分）
- 股票池：default（全市场）
- 操作按钮：开始回测 / 对比回测

## 结果态 UI（三栏布局）

### 左栏：绩效概览
- 核心指标卡片：总收益率、年化收益率、最大回撤、夏普比率、胜率、盈亏比
- 详细指标：总交易次数、平均持仓天数、最大连续亏损、年化波动率、Calmar比率
- 基准对比：基准收益率、超额收益率

### 中栏：图表区
- 资金曲线图（收益率%）+ 基准曲线
- 月度收益柱状图

### 右栏：交易明细
- 交易记录表（日期、代码、名称、方向、价格、数量、原因）
- 双击股票行 → 弹出 TradeDetailDialog

## 核心方法

| 方法 | 说明 |
|------|------|
| `reload_templates()` | 刷新模板下拉框 |
| `prefill_template(template_id)` | 从模板页跳转时预填模板 |
| `_start_backtest()` | 构建 BacktestConfig → 启动 Worker |
| `_start_compare_backtest()` | 对比模式：分别运行 CLOSE 和 NEXT_OPEN 两种时机 |
| `_on_backtest_finished()` | 结果处理 → 渲染绩效 + 图表 + 明细 |
| `_render_metrics()` | 渲染绩效指标卡片 |
| `_render_equity_curve()` | 绘制资金曲线 |
| `_render_trades_table()` | 填充交易明细表 |
| `_export_report()` | 导出 Markdown 报告 |
| `_export_trades_csv()` | 导出交易明细 CSV |

## 缓存机制

- 回测结果缓存：`core/backtest/cache.py`，基于配置参数 SHA-256 hash
- 信号表缓存：`core/backtest/signal_cache.py`，仅与选股条件+股票池+时间范围相关

## 模块依赖

- `BacktestEngine` — 回测引擎
- `BacktestConfig / BacktestResult / BuyTiming` — 数据模型
- `calculate_metrics` — 绩效计算
- `generate_markdown_report / export_trades_csv / export_snapshots_csv` — 报告导出
- `SELL_STRATEGY_REGISTRY` — 卖出策略注册表
- `get_cached_result / save_cached_result` — 结果缓存
- `TemplateService` — 模板服务
- `StockChartWidget` — K线图表
- `load_daily_csv` — 数据加载
- `start_worker` — 后台线程
