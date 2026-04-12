# 回测系统文档

## 模块概览

回测系统实现了完整的策略回测框架，支持基于通达信选股表达式的买入信号生成、多种卖出策略、交易成本模拟、绩效统计、参数敏感性分析和结果缓存。

### 文件清单

| 文件 | 职责 |
|------|------|
| `core/backtest/engine.py` | 回测引擎：时间步进主循环、买卖执行、快照记录 |
| `core/backtest/models.py` | 数据模型：配置、持仓、交易记录、快照、绩效指标、回测结果 |
| `core/backtest/sell_strategy.py` | 卖出策略：基类 + DefaultSellStrategy + BrickChartSellStrategy |
| `core/backtest/metrics.py` | 绩效统计器：收益率、回撤、夏普、胜率等指标计算 |
| `core/backtest/report.py` | 报告生成器：Markdown 报告 + CSV 交易/快照导出 |
| `core/backtest/cache.py` | 结果缓存：基于配置 SHA-256 hash 的 pickle 缓存 |
| `core/backtest/sensitivity.py` | 参数敏感性分析：网格搜索卖出策略参数组合 |
| `app/pages/backtest_page.py` | 回测页面 UI：配置表单 → 进度弹窗 → 结果三栏展示 |

---

## 核心架构

### 整体数据流

```
用户配置表单 (BacktestPage)
    ↓
BacktestConfig（参数打包）
    ↓
缓存检查 (cache.py) ──→ 命中 → 直接展示结果
    ↓ 未命中
BacktestEngine.run()（主循环）
    ├── 提取交易日序列（从大盘股日线数据）
    ├── 预编译通达信表达式（transpile_tdx_source）
    ├── 缓存股票池（StockPoolManager）
    └── 逐日步进：
        ├── 步骤 0：执行前一日的待买入信号（次日开盘价模式）
        ├── 步骤 1：更新持仓价格 + 卖出策略判断
        ├── 步骤 2：运行选股引擎生成买入信号
        ├── 步骤 3：执行买入
        └── 步骤 4：记录 DailySnapshot
    ↓
BacktestResult（交易记录 + 快照序列）
    ↓
calculate_metrics()（绩效计算）
    ↓
保存缓存 → UI 展示（资金曲线 + 绩效表 + 交易明细）
```

---

## 数据模型 (`models.py`)

### BacktestConfig — 回测配置

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `template_id` / `template_name` | str | "" | 关联的策略模板 |
| `tdx_source` | str | "" | 通达信选股表达式源码 |
| `stock_pool_name` | str | "default" | 股票池名称 |
| `start_date` / `end_date` | str | "" | 回测时间范围 |
| `initial_capital` | float | 1,000,000 | 初始资金（元） |
| `position_size` | float | 0.1 | 单只股票仓位比例（10%） |
| `max_positions` | int | 10 | 最大同时持仓数 |
| `commission_rate` | float | 0.0001 | 佣金费率（万1） |
| `min_commission` | float | 5.0 | 最低佣金（元） |
| `stamp_tax_rate` | float | 0.001 | 印花税率（千1，仅卖出） |
| `buy_timing` | BuyTiming | NEXT_OPEN | 买入时机（收盘价/次日开盘价） |
| `sell_strategy_name` | str | "default" | 卖出策略名称 |
| `sell_strategy_params` | dict | {} | 卖出策略参数 |

### BuyTiming — 买入时机枚举

| 值 | 说明 |
|----|------|
| `CLOSE` | 信号日收盘价买入 |
| `NEXT_OPEN` | 次日开盘价买入（更贴近实际交易） |

### BacktestHolding — 持仓信息

跟踪单只股票的持仓状态，包含 `symbol`、`name`、`quantity`、`cost_price`、`total_cost`、`buy_date` 等字段。`update_price(price)` 方法用于每日更新当前价格并重新计算盈亏（`pnl_amount`、`pnl_percent`）。`partial_sold` 标记是否已做过分批止盈。

### BacktestTradeRecord — 交易记录

不可变记录，包含 `symbol`、`action`（BUY/SELL）、`price`、`quantity`、`amount`、`commission`、`stamp_tax`、`total_cost`、`trade_date`、`reason`。

### DailySnapshot — 每日快照

记录每个交易日结束时的状态：`total_assets`、`cash`、`holdings_value`、`holdings_count`、`daily_return`、`cumulative_return`、`trades_today`、`holdings_detail`。

### BacktestMetrics — 绩效指标

| 指标 | 说明 |
|------|------|
| `total_return` | 总收益率 |
| `annual_return` | 年化收益率 |
| `max_drawdown` | 最大回撤 |
| `sharpe_ratio` | 夏普比率（无风险利率 3%） |
| `win_rate` | 胜率 |
| `profit_loss_ratio` | 盈亏比 |
| `total_trades` | 完整买卖交易次数 |
| `average_hold_days` | 平均持仓天数 |
| `max_consecutive_losses` | 最大连续亏损次数 |
| `annual_volatility` | 年化波动率 |
| `calmar_ratio` | Calmar 比率（年化收益/最大回撤） |
| `monthly_returns` | 月度收益分布 |
| `benchmark_return` | 基准总收益率 |
| `benchmark_annual_return` | 基准年化收益率 |
| `excess_return` | 超额收益率（策略 - 基准） |

### BacktestResult — 回测结果

聚合类：包含 `config`、`trades`、`snapshots`、`metrics`、`final_cash`、`final_holdings`、`trading_days`、`benchmark_snapshots`。

---

## 回测引擎 (`engine.py`)

### BacktestEngine

#### 构造

```python
BacktestEngine.from_root(root: Path) -> BacktestEngine
```

自动创建 `StockRepository`、`ScreeningEngine`、`StockPoolManager` 三个依赖。

#### 主循环 `run()`

**输入**：`BacktestConfig` + 可选 `progress_callback` + 可选 `cancelled_fn`
**输出**：`BacktestResult`

**预处理阶段**：
1. 创建卖出策略实例（`create_sell_strategy`）
2. 提取交易日序列（`_extract_trading_days`）：从 000001/600000/000002 的日线数据中筛选回测区间内的交易日
3. 初始化数据缓存（`daily_data_cache`）和日期索引缓存（`date_index_cache`）—— 懒加载
4. 缓存股票池（`stock_pool_manager.get_default_pool`）
5. 预编译通达信表达式（`transpile_tdx_source`，只解析一次）

**每日步进（4 步）**：

| 步骤 | 名称 | 逻辑 |
|------|------|------|
| 0 | 执行待买入信号 | 仅 `NEXT_OPEN` 模式：消费前一日的 `pending_buy_signals`，以当日开盘价买入 |
| 1 | 更新持仓 + 卖出判断 | 遍历所有持仓，更新收盘价，调用 `sell_strategy.should_sell()`。`CLEAR` → 全部卖出；`PARTIAL` → 按比例卖出（至少 100 股） |
| 2 | 选股信号 | 调用 `screening_engine.run_fast_for_backtest()`（优先）或 `screening_engine.run()`（回退），获取当日命中的股票列表 |
| 3 | 执行买入 | 遍历命中股票，跳过已持仓，检查仓位上限。`NEXT_OPEN` 模式记录到 `pending_buy_signals`；`CLOSE` 模式当日执行 |
| 4 | 记录快照 | 计算 `total_assets`、`daily_return`、`cumulative_return`，生成 `DailySnapshot` |

#### 买入执行 `_execute_buy_fast()`

1. 检查：已持仓不重复买入、持仓数量上限
2. 获取日线数据（懒加载 + 构建日期索引）
3. 使用 `locate_time_index_fast()`（O(1)）或 `locate_time_index()` 定位当日行
4. 取买入价：`use_open_price=True` 取 `open`，否则取 `close`
5. 计算买入数量：`available_amount = cash * position_size`，向下取整到 100 股
6. 计算交易成本：`amount + commission`（佣金 = max(amount * 费率, 最低佣金)）
7. 资金不足时尝试缩减手数
8. 创建 `BacktestHolding` + 返回 `BacktestTradeRecord`

#### 卖出执行 `_execute_sell()`

计算：卖出金额 - 佣金 - 印花税 = 净到账金额。返回 `BacktestTradeRecord`。

#### 基准数据 `_load_benchmark_snapshots()`

使用 000001/600000/000002 的收盘价作为基准代理，计算每日和累计收益率，生成 `BenchmarkSnapshot` 列表。

#### 性能优化

- **日期索引缓存**：首次加载股票数据时，同步构建 `{date_str: row_index}` 字典，后续日期查找为 O(1)
- **数据缓存**：`daily_data_cache` 避免重复读取 CSV
- **表达式预编译**：`transpile_tdx_source()` 只调用一次
- **股票池预加载**：`cached_pool` 避免每日重复获取

---

## 卖出策略 (`sell_strategy.py`)

### SellStrategy 基类

```python
class SellStrategy(ABC):
    def should_sell(holding, daily_data, current_index) -> SellSignal: ...
    def get_display_params() -> dict[str, str]: ...
```

返回 `SellSignal`：
- `SellAction.HOLD` — 继续持有
- `SellAction.PARTIAL` — 部分卖出（带 `ratio` 比例）
- `SellAction.CLEAR` — 全部清仓

### DefaultSellStrategy

规则简单：收盘价跌破成本价 95% 时止损清仓。

### BrickChartSellStrategy

砖形图专属策略，基于砖型图指标（日涨跌幅 EMA 平滑）：

| 规则类型 | 条件 | 动作 |
|----------|------|------|
| 绿砖止损 | 当前砖值 < 前一日砖值 | CLEAR |
| 破成本止损 | 收盘价 < 成本价 | CLEAR |
| 分批止盈 | 涨幅 >= 阈值（默认 4.5%）且未做过分批 | PARTIAL（默认 25%） |

**砖型图计算**（`_calc_brick_series`）：
1. 计算日涨跌幅：`(close[i] - close[i-1]) / close[i-1] * 100`
2. EMA 平滑（周期 3）：`alpha = 2/(3+1)`，递推计算

### 策略注册表

```python
SELL_STRATEGY_REGISTRY = {
    "default": DefaultSellStrategy,
    "brick_chart": BrickChartSellStrategy,
}
```

`create_sell_strategy(name, params)` 通过名称 + 参数动态创建实例。

---

## 绩效统计 (`metrics.py`)

### calculate_metrics(result) -> BacktestMetrics

**常量**：`TRADING_DAYS_PER_YEAR = 252`，`RISK_FREE_RATE = 0.03`

| 指标 | 计算方法 |
|------|----------|
| 总收益率 | `(期末总资产 / 初始资金) - 1` |
| 年化收益率 | `(1 + 总收益率) ^ (252 / 交易天数) - 1` |
| 年化波动率 | `std(日收益率, ddof=1) * sqrt(252)` |
| 最大回撤 | 遍历快照，跟踪历史峰值，`max((peak - current) / peak)` |
| 夏普比率 | `(年化收益率 - 0.03) / 年化波动率` |
| Calmar 比率 | `年化收益率 / 最大回撤` |

### 交易配对 `_pair_trades()`

将 BUY/SELL 记录按股票分组配对（FIFO），计算每笔完整交易的利润、收益率和持仓天数。用于计算胜率、盈亏比、平均持仓天数、最大连续亏损。

### 月度收益 `_calc_monthly_returns()`

按 `YYYY-MM` 分组快照，计算月初/月末资产差值得到月度收益率，统计月内卖出交易的胜率。

---

## 报告生成 (`report.py`)

| 函数 | 输出 |
|------|------|
| `generate_markdown_report(result)` | Markdown 字符串，包含基本信息、绩效概览、期末资产、月度收益表、交易明细（前 50 条卖出） |
| `export_trades_csv(result, file_path)` | CSV 文件：交易日期/代码/名称/方向/价格/数量/金额/佣金/印花税/实际金额/原因 |
| `export_snapshots_csv(result, file_path)` | CSV 文件：日期/总资产/可用资金/持仓市值/持仓数量/当日收益率/累计收益率 |

---

## 结果缓存 (`cache.py`)

### 缓存机制

- **缓存键**：对 `BacktestConfig` 的所有关键参数（表达式、股票池、时间范围、资金参数、交易成本、买入时机、卖出策略等）做 JSON 序列化后取 SHA-256 前 16 位
- **存储格式**：pickle 序列化，存放在 `.cache/backtest/{hash}.pkl`
- **接口**：
  - `get_cached_result(root, config)` — 命中返回 `BacktestResult`，否则 `None`
  - `save_cached_result(root, config, result)` — 保存并返回文件路径
  - `clear_cache(root)` — 清除所有缓存，返回删除文件数

---

## 参数敏感性分析 (`sensitivity.py`)

### run_sensitivity_analysis()

对两个参数维度（行参数、列参数）进行网格搜索：

1. 遍历 `row_values × col_values` 的所有组合
2. 每组参数修改 `sell_strategy_params`，运行一次完整回测
3. 记录每组参数的 `total_return`、`annual_return`、`sharpe_ratio`、`max_drawdown`、`win_rate`、`total_trades`
4. 按夏普比率选出最优参数组合

**输出**：`SensitivityResult`，包含参数矩阵 `cells`（二维数组）和 `best_cell`。

**典型使用**：搜索 BrickChartSellStrategy 的 `partial_profit_threshold`（止盈阈值）和 `partial_sell_ratio`（卖出比例）的最优组合。

---

## 回测页面 UI (`backtest_page.py`)

### 页面状态

使用 `QStackedWidget` 实现两态切换：
- **配置态**（index=0）：参数表单
- **结果态**（index=1）：三栏布局展示

### 配置态表单

| 分组 | 控件 | 说明 |
|------|------|------|
| 策略模板 | QComboBox | 从 TemplateService 加载模板列表 |
| 时间范围 | QDateEdit × 2 | 开始/结束日期（日历弹窗） |
| 资金参数 | QSpinBox × 3 | 初始资金、单只仓位%、最大持仓数 |
| 交易成本 | QComboBox + QLabel | 佣金费率（万1/1.5/2/3）、印花税（固定千1） |
| 买入策略 | QRadioButton × 2 + QCheckBox | 收盘价/次日开盘价 + 对比模式 |
| 卖出策略 | QComboBox + 参数面板 | 策略选择 + brick_chart 专属参数（止盈阈值、卖出比例） |
| 操作 | QPushButton × 2 | 开始回测、参数敏感性分析 |

### 模板联动

- 模板名称包含"砖"字时，自动切换卖出策略为 `brick_chart`
- 卖出策略切换时，动态显示/隐藏砖形图参数面板

### 回测执行流程

```
_on_start_backtest()
    ├── _build_config_from_form() → BacktestConfig
    ├── 对比模式？
    │   ├── 是 → _start_compare_backtest()
    │   │       分别构建 CLOSE 和 NEXT_OPEN 两个 config
    │   │       检查两个缓存 → 全命中则直接展示
    │   │       否则依次运行两次回测
    │   └── 否 → _start_single_backtest()
    │           检查缓存 → 命中则直接展示
    │           否则创建 BacktestWorker → start_worker() 后台执行
    └── 进度弹窗 BacktestProgressDialog
        显示：进度条 + 当前日期 + 总资产 + 今日交易数
```

### 结果态三栏布局

| 栏位 | 内容 |
|------|------|
| 左栏 | 绩效概览表格（QTableWidget）：总收益率、年化收益率、最大回撤、夏普比率等 + 基准对比指标 + 返回按钮 |
| 中栏 | 资金曲线图（pyqtgraph PlotWidget）+ 基准线叠加 + 初始资金参考线 + 月度收益表格 + 导出按钮 |
| 右栏 | 交易明细表格：日期/代码/名称/方向/价格/数量/原因 |

### 对比模式

- 依次运行「收盘价买入」和「次日开盘价买入」两组回测
- 绩效表格变为三列（指标/收盘价/次日开盘价）
- 资金曲线图绘制两条线（蓝色 + 橙色）+ 基准虚线

### 参数敏感性分析 UI

1. `SensitivityConfigDialog`：配置止盈阈值和卖出比例的搜索范围（最小值/最大值/步长），实时显示预估组合数
2. `SensitivityWorker`：后台执行网格搜索
3. `SensitivityResultDialog`：展示结果矩阵表格，支持切换展示指标（总收益率/年化收益率/夏普比率/最大回撤/胜率/交易次数），热力图着色（红→黄→绿），高亮最优参数

---

## 交易成本模型

| 费用类型 | 收取方式 | 计算公式 |
|----------|----------|----------|
| 买入佣金 | 买入时 | `max(成交金额 × 佣金费率, 最低佣金)` |
| 卖出佣金 | 卖出时 | `max(成交金额 × 佣金费率, 最低佣金)` |
| 印花税 | 仅卖出时 | `成交金额 × 印花税率` |

买入总成本 = 成交金额 + 佣金
卖出净到账 = 成交金额 - 佣金 - 印花税

### 买入数量计算

1. 可用金额 = `cash × position_size`
2. 股数 = `int(可用金额 / 买入价 / 100) × 100`（向下取整到手）
3. 最少 100 股，否则跳过
4. 资金不足时尝试缩减手数

---

## 关键依赖

| 依赖 | 用途 |
|------|------|
| `core.screening.engine.ScreeningEngine` | 选股引擎，生成买入信号（`run_fast_for_backtest` 回测专用快速接口） |
| `core.expression.parser.transpiler` | 通达信表达式预编译 |
| `core.data.repository.StockRepository` | 日线数据读取 |
| `core.data.time_index` | 日期索引构建与查找 |
| `core.stock_pool.manager.StockPoolManager` | 股票池管理 |
| `core.templates.TemplateService` | 策略模板管理 |
| `app.utils.start_worker` | 统一的后台线程管理 |
