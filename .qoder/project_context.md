# StockViewer 项目上下文

更新时间：2026-05-23

## 项目概述

StockViewer 是一个基于 PySide6 + pyqtgraph 的本地股票日线桌面查看器，支持通达信选股条件智能选股、砖形图定式验证、模拟交易训练、主板评分、数据采集分析。

**技术栈：** PySide6 + pyqtgraph + pandas + numpy + numba(可选) + Tushare API + SQLite

**入口：** `python -m run` 或 `python run.py`

---

## 七个主要页面

| 页面 | 文件 | 职责 |
|------|------|------|
| 看盘页 | `app/pages/market_page.py` + `market_workers.py` | 股票列表搜索、图表查看、快速选股、数据更新 |
| 模板页 | `app/pages/template_page.py` | 选股模板 CRUD 管理 |
| 设置页 | `app/pages/settings_page.py` | Token 配置、图表参数、触发批量更新 |
| 选股页 | `app/pages/screening_page.py` + `screening_trade_controller.py` | 两态设计（配置→结果），含模拟交易（T+1） |
| 统计页 | `app/pages/stats_page.py` + `app/pages/stats/` | API数据采集+持仓分析+收益图表 |
| 定式验证页 | `app/pages/brick_pattern_page.py` + `app/pages/brick_pattern/` | 砖形图定式批量验证+回测评分 |
| 评分诊断页 | `app/pages/scoring_page.py` | 主板多因子评分+三窗口回填诊断 |

## 图表指标（价格面板固定 + 可选副图）

图表 (`app/widgets.py`) 通过 Mixin 拆分：HoverMixin / PanelsMixin / RangesMixin / SubChartsMixin，所有面板 X 轴联动。

| 面板 | 默认 | 计算 |
|------|------|------|
| K线(价格) | ✅固定 | 蜡烛图 + 趋势EMA(EMA(C,10),10) + 多空MA均值 |
| 成交额 | ✅ | 红绿柱(亿)，volume÷10000 |
| 砖型差值 | ✅ | HHV/LLV(4)+SMA多步 |
| KDJ | ✅ | RSV→SMA→K,D,J=3K-2D |
| 单针下20 | — | 短期(3)/中期(14)/长期(20) LLV/HHV 百分比 |
| MACD | — | EMA(12)-EMA(26)→DIFF/DEA/MACD柱 |

---

## 模块架构

### 应用层 (app/)

```
app/
├── main.py / main_window.py   # 入口 + 主窗口(7页面)
├── pages/                     # 7个页面 + 辅助子包(stats/, brick_pattern/)
│   ├── market_page.py + market_workers.py
│   ├── screening_page.py + screening_trade_controller.py
│   ├── stats_page.py + stats/(constants/dialogs/widgets/workers)
│   ├── brick_pattern_page.py + brick_pattern/(dialogs/helpers/workers)
│   ├── scoring_page.py
│   ├── template_page.py / settings_page.py
├── components/settings_form.py
├── dialogs/template_editor_dialog.py
├── services/settings_service.py
├── stats/                     # 数据采集核心(analyzer/config_loader/requester/storage)
├── utils/thread_manager.py
├── widgets.py                 # StockChartWidget 主组件(聚合Mixin)
├── chart_widget_hover.py      # HoverMixin
├── chart_widget_panels.py     # PanelsMixin
├── chart_widget_ranges.py     # RangesMixin
├── chart_widget_subcharts.py  # SubChartsMixin — 副图动态切换
├── chart_layout.py            # 布局 dataclass + 工厂 + SubChartType
├── chart_primitives.py        # K线/砖型图图元
├── chart_indicators.py        # EMA/MA/KDJ/Brick/MACD/Needle20 计算
├── chart_overlays.py          # HTML 标签构建
├── chart_ranges.py / chart_interaction.py
├── mini_chart.py              # MiniCandleChart 迷你K线图
├── progress_dialogs.py        # 通用进度对话框
├── data_loader.py / history_updater.py / tushare_client.py
```

### 核心层 (core/)

```
core/
├── data/                      # SQLite数据库(database.py) + IO(io.py) + 迁移(migration.py) + 时间索引
├── expression/                # 通达信条件解析(词法/语法/AST/求值)
├── indicators/                # 指标注册表 + 内置实现 + 底层算法(algorithms.py, Numba JIT)
├── models/                    # 模板/选股/交易/股票池/砖形图定式 模型
├── screening/                 # 选股引擎 + 服务 + 缓存 + 砖形图定式子包(brick_pattern/)
├── scoring/                   # 主板评分系统(engine/cross_section/factor_health/regime/storage)
├── stock_pool/                # 股票池管理
├── templates/                 # 模板服务 + 存储
├── trade/simulator.py         # 模拟交易(买卖/持仓/结算)
└── utils/                     # 日期/字符串工具
```

---

## 修改定位规则

| 改什么 | 看哪里 |
|--------|--------|
| 看盘页 | `app/pages/market_page.py` + `market_workers.py` |
| 选股页骨架 | `app/pages/screening_page.py` |
| 模拟交易UI/控制器 | `app/pages/screening_trade_controller.py` |
| 统计页/数据采集 | `app/pages/stats_page.py` + `app/stats/` |
| 定式验证页 | `app/pages/brick_pattern_page.py` + `app/pages/brick_pattern/` |
| 评分诊断页 | `app/pages/scoring_page.py` |
| 图表主组件/hover | `app/widgets.py` + `chart_widget_hover.py` |
| 图表副图切换 | `app/chart_widget_subcharts.py` |
| 指标计算(EMA/KDJ/Brick/MACD/Needle20) | `app/chart_indicators.py` |
| 通达信解析 | `core/expression/` |
| 选股引擎 | `core/screening/engine.py` |
| 砖形图定式引擎 | `core/screening/brick_pattern_engine.py` + `core/screening/brick_pattern/` |
| 模拟交易引擎 | `core/trade/simulator.py` |
| 评分引擎 | `core/scoring/` |
| 数据库/IO | `core/data/database.py` + `core/data/io.py` |
| 模板服务 | `core/templates/service.py` |
| 线程管理 | `app/utils/thread_manager.py` |

---

## 数据约定

- `volume` = 成交额（万元），展示换算为亿
- 数据存储：SQLite 为主（`data/market.db`、`data/scoring.db`），CSV 为兼容层
- `symbol` 补齐 6 位，`date` 转 datetime 升序
- 看盘页 X 轴：30~150 天（可配置）
- 选股页 X 轴：固定 90 天，禁用拖动缩放

## 稳定性原则

1. 切股不重建 PlotWidget，只更新数据
2. 防重入：`_loading_plot`、`_updating_range`
3. hover 联动是交互核心（`HoverMixin`）
4. 线程互斥：`start_worker()` 统一管理
5. 选股缓存：`screen_with_cache()` 支持缓存命中和断点续选
6. 子图切换不重建面板（`SubChartsMixin`）
