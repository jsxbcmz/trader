# 选股页 ScreeningPage

**文件：** `app/pages/screening_page.py` (~1202 行)

## 页面定位

两态页面设计（QStackedWidget）：配置态 → 结果态。包含完整的模拟交易功能。

## 类结构

### ScreeningWorker(QtCore.QObject)
与 MarketPage 中同结构的后台选股 Worker。

### ScreeningPage(QtWidgets.QWidget)

**信号：** `statusMessageRequested = Signal(str, int)`
**常量：** `CHART_FIXED_DAYS = 90` — 图表固定展示 90 个交易日

**构造参数：** `root: Path`

## 关键状态变量

| 变量 | 说明 |
|------|------|
| `screening_service` | ScreeningService 实例 |
| `template_service` | TemplateService 实例 |
| `_screening_matches` | 选股命中结果列表 |
| `_target_date` | 选股目标日期字符串 |
| `_simulator` | `TradeSimulator` 实例 |
| `_initial_capital` | 初始资金（默认 10 万） |
| `_available_capital` | 当前可用资金 |
| `_current_sim_date` | 模拟当前日期 |
| `_current_symbol` | 当前查看股票代码 |
| `_current_df` | 当前股票截止模拟日期的 DataFrame |
| `_trade_marker_items` | 图表上的 B/S 标记 item 列表 |
| `_is_at_open` | 是否处于开盘阶段 |

## 页面结构

### 配置态（页面 0）
- 居中表单：选股日期（含随机日期按钮）+ 条件模板下拉 + 确认按钮
- 随机日期策略：多只样本股票交叉验证确认开市日，限制 >= 2020-01-01，排除最近 60 个交易日

### 结果态（页面 1），三栏布局
- 左栏：选股结果列表 + 持有股票列表 + 返回按钮
- 中栏：StockChartWidget 图表（禁用拖动缩放，仅保留 hover）
- 右栏：模拟交易操作面板

## 模拟交易核心方法

| 方法 | 说明 |
|------|------|
| `_on_advance_day()` | 日推进：开盘 → 收盘 → 下一天开盘循环 |
| `_advance_to_next_open()` | 推进到下一天开盘 |
| `_advance_to_close()` | 推进到当天收盘 |
| `_execute_buy(price_field, price_label)` | 统一买入（检查资金 → simulator.buy() → 刷新持仓） |
| `_on_sell()` | 卖出（含 T+1 检查 → simulator.sell()） |
| `_on_settle()` | 结算全部持仓，显示明细弹窗 |
| `_redraw_trade_markers()` | 在图表上绘制 B 三角/S 倒三角标记 |
| `_refresh_holding_table()` | 刷新持仓列表表格 |
| `_refresh_trade_summary()` | 刷新交易统计摘要 |
| `_update_holding_prices()` | 更新持仓当前价格 |

## 模拟交易规则

- 初始资金设置 → 开始训练
- 日推进：「下一天」→ 开盘阶段 → 「快进到收盘」→ 收盘阶段
- 开盘阶段：K线用开盘价替代收盘价，可开盘价买入
- 收盘阶段：展示完整 K线，可收盘价买入
- 卖出：遵循 A 股 T+1 规则（当天买入不可当天卖出）
- 买卖标记：图表上绘制 B▲/S▼ 标记
- 结算：显示总投入/市值/盈亏明细

## 其他核心方法

| 方法 | 说明 |
|------|------|
| `reload_templates()` | 刷新模板下拉 |
| `_on_confirm()` | 点击"开始选股" → 启动 Worker |
| `_on_screening_finished()` | 收集结果 → 切换到结果面板 |
| `_on_stock_selected()` | 选股结果选中 → 加载图表 |
| `_on_holding_selected()` | 持仓列表选中 → 加载图表 |
| `_load_chart_for_current_symbol()` | 加载日线截止模拟日期，渲染图表 |
| `_set_chart_visible_range(df_up_to_date)` | 设置图表 X 轴可见范围为最后 CHART_FIXED_DAYS 天 |
| `_on_random_date()` | 随机选取合法交易日 |
| `_on_back_to_config()` | 返回配置面板（有持仓时弹窗确认） |
| `_on_reset()` | 重置模拟交易状态 |

## 模块依赖

- `ScreeningService / TemplateService` — 选股逻辑
- `TradeSimulator` — 模拟交易核心
- `TradeAction` 枚举 — 区分买卖记录
- `StockChartWidget` — K线图
- `ScreeningProgressDialog` — 进度弹窗
- `load_daily_csv / load_stock_list` — 数据加载
- `start_worker` — 后台线程
- `pyqtgraph.TextItem` — 图表标记
