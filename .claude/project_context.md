# StockViewer 项目上下文文档

更新时间：2026-04-02

## 项目定位

StockViewer 是一个基于 PySide6 + pyqtgraph 的本地股票日线桌面查看器，支持：
- 股票日线图表四联图展示（K线、成交额、砖型差值、KDJ）
- 通达信选股条件解析与智能选股
- 选股模板管理与批量选股
- 模拟交易训练（带 T+1 规则）
- 本地历史数据管理

## 技术栈

- GUI: PySide6 (Qt)
- 绑图: pyqtgraph
- 数据处理: pandas, numpy
- 计算加速: numba (可选 JIT)
- 中文搜索: pypinyin（可选）
- 数据源: Tushare API

## 启动方式

```bash
python -m run
# 或
python run.py
```

---

## 四个主要页面

### 页面 0：看盘页 MarketPage (`app/pages/market_page.py`)

主力页面，左右分栏布局（QSplitter 1:3）。

**左侧面板：**
- 展开/收起设置面板（Tushare Token、图表可视天数配置）
- 更新全部股票按钮（启动后台 UpdateWorker）
- 搜索框：支持代码/名称/拼音首字母/行业/地区，空格分词多条件 AND
- 行业下拉筛选
- 选股面板：选择模板 + 日期 → 执行选股 → 选股结果表（6列）
- 全部股票表（3列：代码、名称、行业）

**右侧面板：**
- StockChartWidget 四联图组件

**核心链路：**
```
搜索 → apply_filter() → on_select() → _load_symbol() → chart.set_daily() → onHover() → 状态栏
选股 → run_screening() → ScreeningWorker(线程) → populate_screening_results()
更新 → start_update_all() → UpdateWorker(线程) → 进度弹窗
```

**关键状态：** `_last_selected_symbol`（持久化到 QSettings），两个后台线程互斥控制

---

### 页面 1：模板页 TemplatePage (`app/pages/template_page.py`)

选股模板的 CRUD 管理页面，左右分栏。

**功能：** 新建、编辑、复制、删除模板。左侧模板列表，右侧详情展示（名称、描述、通达信条件代码）。

**信号：** `templatesChanged` 信号联动 MarketPage 和 ScreeningPage 刷新模板下拉。

---

### 页面 2：设置页 SettingsPage (`app/pages/settings_page.py`)

全局配置页面，使用 SettingsFormWidget 复用组件。

**配置项：** Tushare Token、图表最小/最大可视天数。
**操作：** 保存设置（信号传递到 MainWindow 统一处理）、触发批量更新。

---

### 页面 3：选股页 ScreeningPage (`app/pages/screening_page.py`)

两态页面设计（QStackedWidget）：配置态 → 结果态。

**配置态（页面 0）：**
- 居中表单：选股日期（含随机日期按钮）+ 条件模板下拉 + 确认按钮
- 随机日期策略：多只样本股票交叉验证确认开市日，限制 ≥ 2020-01-01，排除最近 60 个交易日

**结果态（页面 1），三栏布局：**
- 左栏：选股结果列表 + 持有股票列表 + 返回按钮
- 中栏：StockChartWidget 四联图（禁用拖动缩放，仅保留 hover）
- 右栏：模拟交易操作面板

**模拟交易功能：**
- 初始资金设置 → 开始训练
- 日推进机制：「下一天」→ 开盘阶段 → 「快进到收盘」→ 收盘阶段
- 开盘阶段：K线用开盘价替代收盘价，可开盘价买入
- 收盘阶段：展示完整 K线，可收盘价买入
- 卖出：遵循 A股 T+1 规则（当天买入不可当天卖出）
- 买卖标记：图表上绘制 B▲/S▼ 标记
- 结算：显示总投入/市值/盈亏明细
- 图表固定显示 CHART_FIXED_DAYS=90 个交易日

**选股缓存：** 使用 `screen_with_cache()` 支持缓存命中和断点续选

---

## 四联图指标详解

四联图是本项目的核心可视化组件，由 `StockChartWidget` (`app/widgets.py`) 管理，四个面板 X 轴联动。

### 面板 1：K线图（Price Panel，占比 3/6）

**图形项：**
- `CandlestickItem`：日K线蜡烛图（红涨绿跌）
- `zx_short_trend`：知行短期趋势线（白色，EMA(EMA(close,10),10)）
- `zx_long_short`：知行多空线（金色，MA(14,28,57,114) 均值）
- 十字光标（vLine + hLine）
- 信息浮窗（infoText）：日期、收盘价、涨跌幅、成交额
- Y轴价格标签（yValueText）：跟随鼠标显示价格
- 指标标签（indicatorLabel）：左上角显示趋势线和多空线数值

**知行短期趋势线计算：** `ema(ema(close, 10), 10)` — 双重 EMA 平滑
**知行多空线计算：** `mean(MA(14), MA(28), MA(57), MA(114))` — 四周期均线均值

### 面板 2：成交额图（Volume Panel，占比 1/6）

**图形项：** BarGraphItem 柱状图，红色（收≥开）/ 绿色（收<开），单位亿元
**数据转换：** 原始 volume（万元）÷ 10000 = 亿元

### 面板 3：砖型差值图（Brick Panel，占比 1/6）

**图形项：**
- `BrickDeltaItem`：自定义图元，绘制砖型差值柱（正值红色、负值绿色）
- 零线（brickZeroLine）
- 差值标签（brickDeltaLabel）：显示当前 brick 与前一日 brick 的差值

**计算逻辑（`compute_brick_indicator`）：**
```
HHV4 = 最近4日最高价
LLV4 = 最近4日最低价
SPAN = HHV4 - LLV4
var1a = (HHV4 - CLOSE) / SPAN * 100 - 90
var2a = SMA(var1a, 4, 1) + 100
var3a = (CLOSE - LLV4) / SPAN * 100
var4a = SMA(var3a, 6, 1)
var5a = var4a + 100
var6a = var5a - var2a
brick = max(var6a - 4, 0)  // 仅保留 > 4 的部分
```

### 面板 4：KDJ 指标图（KDJ Panel，占比 1/6）

**图形项：**
- K 线（白色）、D 线（金色）、J 线（紫色）
- 三条参考线：20（绿色虚线）、50（青色虚线）、80（红色虚线）
- KDJ 标签（kdjLabel）：显示当前 K/D/J 数值

**计算逻辑（`compute_kdj_indicator`）：**
```
RSV = (CLOSE - LLV(LOW,9)) / (HHV(HIGH,9) - LLV(LOW,9)) * 100
K = SMA(RSV, 3, 1)
D = SMA(K, 3, 1)
J = 3K - 2D
```

**四联图交互机制：**
- 鼠标在任一面板移动，四个面板的十字光标 X 轴同步
- hover 联动更新：指标标签、砖型差值标签、KDJ 标签、信息浮窗、状态栏
- Y 轴价格标签仅在 Price Panel 显示
- X 轴范围锁定：`_clamp_xrange()` 防止超出数据范围
- Y 轴自适应：`_update_visible_yrange()` 随可视范围自动调整

---

## 模块架构

### 应用层 (app/)

```
app/
├── main.py                    # 应用入口
├── main_window.py             # 主窗口，4页面切换（菜单栏+状态栏）
├── pages/
│   ├── market_page.py         # 页面0：看盘页
│   ├── template_page.py       # 页面1：模板页
│   ├── settings_page.py       # 页面2：设置页
│   └── screening_page.py      # 页面3：选股页（含模拟交易）
├── components/
│   └── settings_form.py       # 设置表单复用组件
├── dialogs/
│   └── template_editor_dialog.py
├── services/
│   └── settings_service.py    # QSettings 持久化
├── utils/
│   └── thread_manager.py      # start_worker 统一线程管理
├── widgets.py                 # StockChartWidget + 进度弹窗
├── chart_layout.py            # PlotBundle/Items dataclass + 工厂函数
├── chart_primitives.py        # CandlestickItem, BrickDeltaItem, DateAxisItem
├── chart_indicators.py        # 指标计算（EMA, MA, SMA, KDJ, Brick）
├── chart_overlays.py          # HTML 构建（信息浮窗、标签）
├── chart_ranges.py            # 范围 clamp 逻辑
├── chart_interaction.py       # StockChartViewBox 鼠标交互
├── data_loader.py             # CSV 加载
├── history_updater.py         # 历史数据增量更新
└── tushare_client.py          # Tushare API 客户端
```

### 核心层 (core/)

```
core/
├── data/
│   ├── repository.py          # 股票数据访问
│   ├── base_json_repository.py # JSON 文件读写基类
│   └── time_index.py          # 时间索引定位
├── expression/
│   ├── parser/                # 词法/语法解析/AST 转换
│   ├── nodes.py               # AST 节点定义
│   └── evaluator.py           # 表达式求值引擎
├── indicators/
│   ├── registry.py            # 指标函数注册表
│   ├── builtin.py             # 内置指标实现
│   └── tdx_compat.py          # 通达信兼容
├── models/
│   ├── template.py            # ScreeningTemplate
│   ├── screening.py           # ScreeningRequest/Result
│   ├── trade.py               # TradeRecord, TradeAction, HoldingInfo
│   ├── stock_pool.py          # StockPool
│   ├── market.py              # 股票基础信息
│   └── expression.py          # 表达式模型
├── screening/
│   ├── engine.py              # ScreeningEngine 选股执行引擎
│   ├── service.py             # ScreeningService（含 screen_with_cache）
│   ├── cache_models.py        # 缓存数据模型
│   ├── cache_repository.py    # 缓存 JSON 读写
│   ├── error_policy.py        # 错误处理策略
│   ├── result_models.py       # 结果模型
│   └── result_formatter.py    # 结果格式化
├── stock_pool/
│   └── manager.py             # StockPoolManager
├── templates/
│   ├── service.py             # TemplateService（含 build_screening_request）
│   ├── repository.py          # TemplateRepository
│   └── builtin.py             # 内置模板
├── trade/
│   └── simulator.py           # TradeSimulator（买卖/持仓/结算）
└── utils/
    ├── dates.py               # 日期工具
    └── strings.py             # 字符串工具
```

---

## 数据文件

| 路径 | 说明 |
|------|------|
| `stocklist.csv` | 股票列表（ts_code, symbol, name, area, industry） |
| `stock_daily_data/{symbol}.csv` | 个股日线（date, open, high, low, close, volume） |
| `templates.json` | 选股模板存储 |
| `screening_cache/screening_cache.json` | 选股缓存 |

---

## 修改定位规则

| 需求 | 优先查看 |
|------|----------|
| 改看盘页筛选/表格/状态栏 | `app/pages/market_page.py` |
| 改选股页/模拟交易 | `app/pages/screening_page.py` |
| 改模板管理界面 | `app/pages/template_page.py` |
| 改设置页 | `app/pages/settings_page.py` |
| 改四联图/十字线/tooltip/hover | `app/widgets.py` |
| 改图表布局/面板结构 | `app/chart_layout.py` |
| 改 K线/砖型图图元绘制 | `app/chart_primitives.py` |
| 改指标计算（EMA/KDJ/Brick等） | `app/chart_indicators.py` |
| 改信息浮窗/标签 HTML | `app/chart_overlays.py` |
| 改通达信条件解析 | `core/expression/` |
| 改选股引擎逻辑 | `core/screening/engine.py` |
| 改选股缓存 | `core/screening/cache_*.py` |
| 改模板服务 | `core/templates/service.py` |
| 改模拟交易逻辑 | `core/trade/simulator.py` |
| 改线程管理 | `app/utils/thread_manager.py` |
| 改 CSV 加载/字段 | `app/data_loader.py` |
| 改数据更新/Tushare | `app/history_updater.py` |

---

## 稳定性原则

1. **切股时不重建 PlotWidget** — 只更新数据、日期映射和可视范围
2. **防重入标志** — `_loading_plot`、`_updating_range`
3. **hover 联动核心** — 十字线、浮窗、价格标签、状态栏、指标标签都围绕 `_on_mouse_moved()` 联动
4. **线程互斥** — 更新和选股各自独立线程，通过 `start_worker()` 统一管理生命周期
5. **选股缓存** — `screen_with_cache()` 支持缓存命中直接返回和断点续选

---

## 数据约定

- `volume` 实际语义为**成交额（万元）**，展示时换算为**亿**
- `symbol` 需补齐为 6 位字符串
- `date` 转 datetime 并升序排列
- 看盘页 X 轴范围：最小 30 天，最大 150 天（可配置）
- 选股页 X 轴范围：固定 90 天，禁用拖动缩放
