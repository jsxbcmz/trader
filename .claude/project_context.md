# StockViewer 项目上下文文档

更新时间：2026-05-23

## 项目定位

StockViewer 是一个基于 PySide6 + pyqtgraph 的本地股票日线桌面查看器，支持：
- 股票日线图表展示（K线 + 可选副图面板：成交额、砖型差值、KDJ、MACD、单针下20）
- 通达信选股条件解析与智能选股
- 选股模板管理与批量选股
- 模拟交易训练（带 T+1 规则）
- 砖形图定式批量验证与回测评分
- 主板评分系统（多因子打分 + 三窗口回填诊断）
- 数据采集与持仓分析统计
- 本地历史数据管理（SQLite + CSV 双模式）

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

## 七个主要页面

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
- StockChartWidget 图表组件（价格面板 + 可选副图面板）

**核心链路：**
```
搜索 → apply_filter() → on_select() → _load_symbol() → chart.set_daily() → onHover() → 状态栏
选股 → run_screening() → ScreeningWorker(线程) → populate_screening_results()
更新 → start_update_all() → UpdateWorker(线程) → 进度弹窗
```

**关键状态：** `_last_selected_symbol`（持久化到 QSettings），两个后台线程互斥控制

**辅助模块：** `app/pages/market_workers.py` — UpdateWorker / IndustryDownloadWorker / build_name_initials

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
- 中栏：StockChartWidget 图表（禁用拖动缩放，仅保留 hover）
- 右栏：模拟交易操作面板（由 `ScreeningTradeController` 管理）

**模拟交易功能（`app/pages/screening_trade_controller.py`）：**
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

### 页面 4：统计页 StatsPage (`app/pages/stats_page.py`)

数据采集与持仓分析页面。

**功能：** API 数据采集、持仓分析、收益图表展示。
**辅助模块：** `app/pages/stats/` — constants、dialogs、widgets、workers 子包；`app/stats/` — analyzer、config_loader、requester、storage

---

### 页面 5：定式验证页 BrickPatternPage (`app/pages/brick_pattern_page.py`)

砖形图定式批量验证页面。

**功能：** 砖形图定式识别、批量验证、回测评分。
**辅助模块：** `app/pages/brick_pattern/` — dialogs、helpers、workers 子包

---

### 页面 6：评分诊断页 ScoringPage (`app/pages/scoring_page.py`)

主板评分系统诊断页面。

**功能：** 主板多因子评分 + 三窗口回填诊断展示。
**核心引擎：** `core/scoring/` 评分引擎包

---

## 图表指标详解

图表系统由 `StockChartWidget` (`app/widgets.py`) 管理，采用 **价格面板（固定）+ 可选副图面板** 架构，所有面板 X 轴联动。
Widget 通过 Mixin 拆分：`HoverMixin`（chart_widget_hover.py）、`PanelsMixin`（chart_widget_panels.py）、`RangesMixin`（chart_widget_ranges.py）、`SubChartsMixin`（chart_widget_subcharts.py）。

### 价格面板（Price Panel，固定，占比动态调整）

**图形项：**
- `CandlestickItem`：日K线蜡烛图（红涨绿跌）
- `zx_short_trend`：知行短期趋势线（白色，EMA(EMA(close,10),10)）
- `zx_long_short`：知行多空线（金色，MA(14,28,57,114) 均值）
- 十字光标（vLine + hLine）
- 信息浮窗（infoText）：日期、收盘价、涨跌幅、成交额
- Y轴价格标签（yValueText）：跟随鼠标显示价格
- 指标标签（indicatorLabel）：左上角显示趋势线和多空线数值

### 可选副图面板

通过 `SubChartType` 枚举和 `set_visible_sub_charts()` 动态切换显示/隐藏，不重建面板。

| 面板 | 默认 | 计算 |
|------|------|------|
| 成交额 | ✅ | 红绿柱(亿)，volume(万元)÷10000 |
| 砖型差值 | ✅ | HHV/LLV(4)+SMA多步，`compute_brick_indicator` |
| KDJ | ✅ | RSV→SMA→K,D,J=3K-2D，`compute_kdj_indicator` |
| 单针下20 | — | 短期(3)/中期(14)/长期(20) LLV/HHV 百分比，`compute_needle20_indicator` |
| MACD | — | EMA(12)-EMA(26)→DIFF/DEA/MACD柱，`compute_macd_indicator` |

### 交互机制

- 鼠标在任一面板移动，所有面板十字光标 X 轴同步
- hover 联动更新：指标标签、砖型差值标签、KDJ/MACD/Needle20 标签、信息浮窗、状态栏
- Y 轴价格标签仅在 Price Panel 显示
- X 轴范围锁定：`_clamp_xrange()` 防止超出数据范围
- Y 轴自适应：`_update_visible_yrange()` 随可视范围自动调整
- 子图切换不重建面板 —— 只控制 show/hide + 重新链接 X 轴

---

## 模块架构

### 应用层 (app/)

```
app/
├── main.py                    # 应用入口
├── main_window.py             # 主窗口，7页面切换（菜单栏+状态栏）
├── pages/
│   ├── market_page.py         # 页面0：看盘页
│   ├── market_workers.py      # 看盘页后台 worker（UpdateWorker/IndustryDownloadWorker）
│   ├── template_page.py       # 页面1：模板页
│   ├── settings_page.py       # 页面2：设置页
│   ├── screening_page.py      # 页面3：选股页（含模拟交易）
│   ├── screening_trade_controller.py  # 选股页模拟交易控制器（T+1/资金/标记/结算）
│   ├── stats_page.py          # 页面4：统计页（数据采集+持仓分析）
│   ├── stats/                 # 统计页辅助子包
│   │   ├── constants.py       # 常量定义
│   │   ├── dialogs.py         # 统计页对话框
│   │   ├── widgets.py         # 统计页小部件
│   │   └── workers.py         # 统计页后台 worker
│   ├── brick_pattern_page.py  # 页面5：定式验证页
│   ├── brick_pattern/         # 定式验证页辅助子包
│   │   ├── dialogs.py         # 定式验证对话框
│   │   ├── helpers.py         # 定式验证辅助函数
│   │   └── workers.py         # 定式验证后台 worker
│   └── scoring_page.py        # 页面6：评分诊断页
├── components/
│   └── settings_form.py       # 设置表单复用组件
├── dialogs/
│   └── template_editor_dialog.py
├── services/
│   └── settings_service.py    # QSettings 持久化
├── stats/                     # 数据采集核心模块
│   ├── analyzer.py            # 数据分析
│   ├── config_loader.py       # API 配置加载
│   ├── requester.py           # API 请求器
│   └── storage.py             # 数据存储
├── utils/
│   └── thread_manager.py      # start_worker 统一线程管理
├── widgets.py                 # StockChartWidget 主组件（聚合 Mixin）
├── chart_widget_hover.py      # HoverMixin — hover 联动逻辑
├── chart_widget_panels.py     # PanelsMixin — 面板数据填充
├── chart_widget_ranges.py     # RangesMixin — 范围管理
├── chart_widget_subcharts.py  # SubChartsMixin — 副图面板动态切换
├── chart_layout.py            # PlotBundle/Items dataclass + 工厂函数 + SubChartType 枚举
├── chart_primitives.py        # CandlestickItem, BrickDeltaItem, DateAxisItem
├── chart_indicators.py        # 指标计算（EMA, MA, SMA, KDJ, Brick, MACD, Needle20）
├── chart_overlays.py          # HTML 构建（信息浮窗、标签）
├── chart_ranges.py            # 范围 clamp 逻辑
├── chart_interaction.py       # StockChartViewBox 鼠标交互
├── mini_chart.py              # MiniCandleChart 迷你K线图组件
├── progress_dialogs.py        # 通用进度对话框（UpdateProgressDialog/ScreeningProgressDialog）
├── data_loader.py             # CSV 加载（兼容层）
├── history_updater.py         # 历史数据增量更新
└── tushare_client.py          # Tushare API 客户端
```

### 核心层 (core/)

```
core/
├── data/
│   ├── database.py            # SQLite 数据库连接管理（MarketDatabase/ScoringDatabase）
│   ├── io.py                  # Data IO — SQLite-backed + 内存缓存
│   ├── migration.py           # CSV → SQLite 一次性迁移脚本
│   ├── repository.py          # 股票数据访问
│   ├── base_json_repository.py # JSON 文件读写基类
│   └── time_index.py          # 时间索引定位
├── expression/
│   ├── parser/                # 词法/语法解析/AST 转换
│   │   ├── lexer.py           # 词法分析器
│   │   ├── parser.py          # 语法解析器
│   │   └── transpiler.py      # AST 转译器
│   ├── nodes.py               # AST 节点定义
│   └── evaluator.py           # 表达式求值引擎
├── indicators/
│   ├── registry.py            # 指标函数注册表
│   ├── builtin.py             # 内置指标实现
│   └── algorithms.py          # 底层算法（Numba JIT 加速，rolling_max/min/SMA 等）
├── models/
│   ├── template.py            # ScreeningTemplate
│   ├── screening.py           # ScreeningRequest/Result
│   ├── trade.py               # TradeRecord, TradeAction, HoldingInfo
│   ├── stock_pool.py          # StockPool
│   ├── market.py              # 股票基础信息
│   └── brick_pattern.py       # 砖形图定式模型
├── screening/
│   ├── engine.py              # ScreeningEngine 选股执行引擎
│   ├── service.py             # ScreeningService（含 screen_with_cache）
│   ├── brick_pattern_engine.py # 砖形图定式选股引擎
│   ├── brick_pattern/         # 砖形图定式子包
│   │   ├── backtest.py        # 定式回测引擎
│   │   ├── detectors.py       # 定式检测器
│   │   ├── helpers.py         # 辅助函数
│   │   ├── pipeline.py        # 定式处理管道
│   │   ├── scoring.py         # 定式评分
│   │   └── scoring_risk.py    # 定式风险评分
│   ├── cache_models.py        # 缓存数据模型
│   ├── cache_repository.py    # 缓存 JSON 读写
│   ├── error_policy.py        # 错误处理策略
│   ├── result_models.py       # 结果模型
│   └── result_formatter.py    # 结果格式化
├── scoring/                   # 主板评分系统
│   ├── engine.py              # 评分引擎
│   ├── cross_section.py       # 截面分析
│   ├── factor_health.py       # 因子健康度
│   ├── main_board_pool.py     # 主板股票池
│   ├── outcomes.py            # 评分结果
│   ├── regime.py              # 市场环境判断
│   └── storage.py             # 评分数据落盘
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
| `data/market.db` | SQLite 主数据库（股票列表 + 个股日线），由 `core/data/database.py` 管理 |
| `data/scoring.db` | SQLite 评分数据库，由 `core/scoring/storage.py` 管理 |
| `stocklist.csv` | 股票列表（兼容旧格式，可通过 migration.py 迁移到 SQLite） |
| `stock_daily_data/{symbol}.csv` | 个股日线（兼容旧格式） |
| `templates.json` | 选股模板存储 |
| `screening_cache/screening_cache.json` | 选股缓存 |
| `output/day_positions.json` | 采集输出：持仓数据 |
| `output/user_keys.json` | 采集输出：用户密钥 |
| `app/stats/api_config.json` | API 采集配置 |

---

## 修改定位规则

| 需求 | 优先查看 |
|------|----------|
| 改看盘页筛选/表格/状态栏 | `app/pages/market_page.py` |
| 改看盘页后台更新/下载 | `app/pages/market_workers.py` |
| 改选股页骨架/图表 | `app/pages/screening_page.py` |
| 改模拟交易逻辑(UI/资金/T+1) | `app/pages/screening_trade_controller.py` |
| 改模拟交易引擎(买卖/持仓/结算) | `core/trade/simulator.py` |
| 改模板管理界面 | `app/pages/template_page.py` |
| 改设置页 | `app/pages/settings_page.py` |
| 改统计页/数据采集 | `app/pages/stats_page.py` + `app/stats/` |
| 改定式验证页 | `app/pages/brick_pattern_page.py` + `app/pages/brick_pattern/` |
| 改评分诊断页 UI | `app/pages/scoring_page.py` |
| 改图表主组件/hover/十字线 | `app/widgets.py` + `app/chart_widget_hover.py` |
| 改图表面板数据填充 | `app/chart_widget_panels.py` |
| 改图表副图切换 | `app/chart_widget_subcharts.py` |
| 改图表范围管理 | `app/chart_widget_ranges.py` |
| 改图表布局/面板结构 | `app/chart_layout.py` |
| 改 K线/砖型图元绘制 | `app/chart_primitives.py` |
| 改指标计算（EMA/KDJ/Brick/MACD/Needle20） | `app/chart_indicators.py` |
| 改信息浮窗/标签 HTML | `app/chart_overlays.py` |
| 改迷你K线图 | `app/mini_chart.py` |
| 改进度对话框 | `app/progress_dialogs.py` |
| 改通达信条件解析 | `core/expression/` |
| 改选股引擎逻辑 | `core/screening/engine.py` |
| 改砖形图定式引擎 | `core/screening/brick_pattern_engine.py` + `core/screening/brick_pattern/` |
| 改选股缓存 | `core/screening/cache_*.py` |
| 改模板服务 | `core/templates/service.py` |
| 改主板评分引擎/落盘/回填 | `core/scoring/` |
| 改线程管理 | `app/utils/thread_manager.py` |
| 改数据库/数据IO | `core/data/database.py` + `core/data/io.py` |
| 改数据迁移 | `core/data/migration.py` |
| 改 CSV 加载(兼容层) | `app/data_loader.py` |
| 改数据更新/Tushare | `app/history_updater.py` |
| 改股票池管理 | `core/stock_pool/manager.py` |
| 改底层算法(Numba JIT) | `core/indicators/algorithms.py` |

---

## 稳定性原则

1. **切股时不重建 PlotWidget** — 只更新数据、日期映射和可视范围
2. **防重入标志** — `_loading_plot`、`_updating_range`
3. **hover 联动核心** — 十字线、浮窗、价格标签、状态栏、指标标签都围绕 `_on_mouse_moved()` 联动
4. **线程互斥** — 更新和选股各自独立线程，通过 `start_worker()` 统一管理生命周期
5. **选股缓存** — `screen_with_cache()` 支持缓存命中直接返回和断点续选
6. **子图切换不重建面板** — 只控制 show/hide + 重新链接 X 轴

---

## 数据约定

- `volume` 实际语义为**成交额（万元）**，展示时换算为**亿**
- `turnover_rate` = 换手率(%)，增量更新时从 daily_basic 接口获取，支持缺失值回填
- `symbol` 需补齐为 6 位字符串
- `date` 转 datetime 并升序排列
- 看盘页 X 轴范围：最小 30 天，最大 150 天（可配置）
- 选股页 X 轴范围：固定 90 天，禁用拖动缩放
- 数据存储：SQLite 为主（`core/data/database.py`），CSV 为兼容层（`app/data_loader.py`）
- 采集输出：`output/day_positions.json`、`output/user_keys.json`
