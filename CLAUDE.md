# StockViewer 项目指引

## 项目定位
PySide6 + pyqtgraph 本地股票日线查看器，支持通达信选股、模拟交易训练、数据采集分析。

## 启动方式
```bash
python -m run  # 或 python run.py
```

## 页面文档

| 页面 | 源文件 | 详细文档 |
|------|--------|----------|
| 看盘页 | `app/pages/market_page.py` | [.claude/pages/market_page.md](.claude/pages/market_page.md) — 股票搜索、四联图、数据更新 |
| 模板页 | `app/pages/template_page.py` | [.claude/pages/template_page.md](.claude/pages/template_page.md) — 模板 CRUD |
| 设置页 | `app/pages/settings_page.py` | [.claude/pages/settings_page.md](.claude/pages/settings_page.md) — Token、图表配置、批量更新 |
| 选股页 | `app/pages/screening_page.py` | [.claude/pages/screening_page.md](.claude/pages/screening_page.md) — 两态选股+模拟交易(T+1) |
| 统计页 | `app/pages/stats_page.py` | [.claude/pages/stats_page.md](.claude/pages/stats_page.md) — API数据采集+持仓分析+收益图表 |

## 模块文档

| 模块 | 详细文档 | 核心文件 |
|------|----------|----------|
| 四联图系统 | [.claude/modules/chart_system.md](.claude/modules/chart_system.md) | `app/widgets.py` + chart_*.py |
| 通达信表达式 | [.claude/modules/expression_system.md](.claude/modules/expression_system.md) | `core/expression/` |
| 选股系统 | [.claude/modules/screening_system.md](.claude/modules/screening_system.md) | `core/screening/` |
| 模拟交易 | [.claude/modules/trade_simulator.md](.claude/modules/trade_simulator.md) | `core/trade/simulator.py` |
| 数据层 | [.claude/modules/data_layer.md](.claude/modules/data_layer.md) | `core/data/` + `app/data_loader.py` + `app/history_updater.py` |
| 指标系统 | [.claude/modules/indicators.md](.claude/modules/indicators.md) | `core/indicators/` |
| 应用框架 | [.claude/modules/app_framework.md](.claude/modules/app_framework.md) | `app/main_window.py` + services + utils + stats |

## 快速定位规则

| 需求 | 优先查看 | 详细文档 |
|------|----------|----------|
| 改看盘页搜索/表格/状态栏 | `app/pages/market_page.py` | pages/market_page.md |
| 改选股页/模拟交易 | `app/pages/screening_page.py` | pages/screening_page.md |
| 改模板管理界面 | `app/pages/template_page.py` | pages/template_page.md |
| 改设置页 | `app/pages/settings_page.py` | pages/settings_page.md |
| 改统计页/数据采集 | `app/pages/stats_page.py` | pages/stats_page.md |
| 改四联图/hover/十字线 | `app/widgets.py` | modules/chart_system.md |
| 改图表布局/面板结构 | `app/chart_layout.py` | modules/chart_system.md |
| 改K线/砖型图元绘制 | `app/chart_primitives.py` | modules/chart_system.md |
| 改指标计算(EMA/KDJ/Brick) | `app/chart_indicators.py` | modules/chart_system.md |
| 改信息浮窗/标签HTML | `app/chart_overlays.py` | modules/chart_system.md |
| 改通达信条件解析 | `core/expression/` | modules/expression_system.md |
| 改选股引擎/缓存 | `core/screening/` | modules/screening_system.md |
| 改模板服务 | `core/templates/service.py` | modules/indicators.md |
| 改模拟交易逻辑 | `core/trade/simulator.py` | modules/trade_simulator.md |
| 改数据加载/更新 | `app/data_loader.py`, `app/history_updater.py` | modules/data_layer.md |
| 改线程管理 | `app/utils/thread_manager.py` | modules/app_framework.md |
| 改数据采集/API请求 | `app/stats/` | pages/stats_page.md |
| 改股票池管理 | `core/stock_pool/manager.py` | modules/screening_system.md |

## 四联图指标速览

| 面板 | 占比 | 计算 |
|------|------|------|
| K线 | 3/6 | 蜡烛图 + 趋势EMA(EMA(C,10),10) + 多空MA均值 |
| 成交额 | 1/6 | 红绿柱(亿) |
| 砖型差值 | 1/6 | HHV/LLV(4)+SMA多步 |
| KDJ | 1/6 | RSV→SMA→K,D,J=3K-2D |

## 交易知识库（交易相关问题必读）

当用户提问涉及交易策略、买卖操作、仓位管理、交易心理等交易相关话题时，**必须先读取以下文件**再回答：

| 文件 | 内容 |
|------|------|
| `.claude/trading_strategies.md` | 双线战法、波段七步法、超短线红包翠战法、仓位管理、买卖规则 |
| `.claude/trading_rules.md` | 交易纪律、止损止盈规则、风控原则 |
| `.claude/trading_psychology.md` | 交易心理、情绪管理、常见误区 |
| `.claude/quick_memory_card.md` | 快速记忆卡片、核心口诀速查 |

> ⚠️ 这些文件定义了用户个人的交易体系，回答时必须以文件内容为准，不要用通用的股票知识替代。

## 个股分析侧重点

当用户要求分析某只股票时，**必须先读取 `.claude/stock_analysis_focus.md`**，按照其中定义的侧重点和分析模板进行分析。

> ⚠️ 该文件定义了用户个人的分析偏好，分析时必须以文件内容为准。

## 数据源约束（严格遵守）
**所有股票数据请求必须且只能使用 Tushare 作为数据源。** 禁止擅自引入 AKShare、baostock、yfinance 或任何其他数据源。除非用户在需求中明确指出要更换数据源，否则一律使用 Tushare。这是硬性规则，不可自行判断"替代方案更好"而更换。

## 数据约定
- `volume` = 成交额(万元)，展示时换算为亿
- 看盘页X轴：30~150天(可配置)；选股页：固定90天
- `symbol` 补齐6位，`date` 转datetime升序排列
- 数据文件：`stocklist.csv`、`stock_daily_data/{symbol}.csv`、`templates.json`、`screening_cache/screening_cache.json`
- 采集输出：`output/day_positions.json`、`output/user_keys.json`

## 稳定性原则
1. 切股不重建 PlotWidget —— 只更新数据和范围
2. 防重入：`_loading_plot`、`_updating_range`
3. hover 联动是交互核心（`_on_mouse_moved()`）
4. `start_worker()` 统一线程管理
5. `screen_with_cache()` 缓存+断点续选
