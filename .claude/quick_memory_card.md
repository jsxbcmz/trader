# StockViewer Quick Memory Card

更新时间：2026-05-23

## 项目定位
PySide6 + pyqtgraph 本地股票日线查看器，支持通达信选股、砖形图定式验证、模拟交易训练、主板评分、数据采集分析。

## 七个页面
| 页面 | 文件 | 核心功能 |
|------|------|----------|
| 看盘页 | `pages/market_page.py` + `market_workers.py` | 股票搜索、图表、快速选股、数据更新 |
| 模板页 | `pages/template_page.py` | 模板 CRUD |
| 设置页 | `pages/settings_page.py` | Token、图表配置 |
| 选股页 | `pages/screening_page.py` + `screening_trade_controller.py` | 两态(配置→结果)、模拟交易(T+1) |
| 统计页 | `pages/stats_page.py` + `pages/stats/` | API数据采集+持仓分析+收益图表 |
| 定式验证页 | `pages/brick_pattern_page.py` + `pages/brick_pattern/` | 砖形图定式批量验证+回测 |
| 评分诊断页 | `pages/scoring_page.py` | 主板评分+三窗口回填诊断 |

## 图表指标（价格面板固定 + 可选副图）
| 面板 | 默认 | 计算 |
|------|------|------|
| K线(价格) | ✅固定 | 蜡烛图 + 趋势EMA(EMA(C,10),10) + 多空MA均值 |
| 成交额 | ✅ | 红绿柱(亿) |
| 砖型差值 | ✅ | HHV/LLV(4)+SMA多步 |
| KDJ | ✅ | RSV→SMA→K,D,J=3K-2D |
| 单针下20 | — | 短期(3)/中期(14)/长期(20) LLV/HHV 百分比 |
| MACD | — | EMA(12)-EMA(26)→DIFF/DEA/MACD柱 |

## 核心层速查
| 模块 | 核心文件 | 职责 |
|------|----------|------|
| 数据层 | `core/data/database.py`, `io.py`, `migration.py` | SQLite存储+IO+迁移 |
| 表达式 | `core/expression/` | 通达信条件解析求值 |
| 指标 | `core/indicators/builtin.py`, `algorithms.py` | MA/EMA/REF/CROSS + Numba加速 |
| 选股 | `core/screening/engine.py` | 选股引擎 |
| 定式 | `core/screening/brick_pattern/` | 砖形图定式检测+回测+评分 |
| 缓存 | `core/screening/cache_*.py` | 选股缓存+断点续选 |
| 模板 | `core/templates/service.py` | 模板服务 |
| 交易 | `core/trade/simulator.py` | 模拟买卖/持仓/结算 |
| 评分 | `core/scoring/` | 主板多因子评分+落盘 |

## 修改定位
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
| 图表面板数据 | `app/chart_widget_panels.py` |
| 图表指标计算 | `app/chart_indicators.py` |
| K线/砖型图元 | `app/chart_primitives.py` |
| 通达信解析 | `core/expression/` |
| 选股引擎 | `core/screening/engine.py` |
| 砖形图定式引擎 | `core/screening/brick_pattern_engine.py` + `core/screening/brick_pattern/` |
| 模拟交易引擎 | `core/trade/simulator.py` |
| 评分引擎 | `core/scoring/` |
| 数据库/IO | `core/data/database.py` + `core/data/io.py` |
| 线程管理 | `app/utils/thread_manager.py` |

## 数据约定
- `volume` = 成交额(万元)，展示亿
- 数据存储：SQLite为主(`data/market.db`、`data/scoring.db`)，CSV为兼容层
- 看盘页X轴：30~150天；选股页：固定90天
- `symbol` 补齐6位

## 稳定性原则
1. 切股不重建 PlotWidget
2. 防重入：`_loading_plot`、`_updating_range`
3. hover 联动是交互核心（`HoverMixin`）
4. `start_worker()` 统一线程管理
5. `screen_with_cache()` 缓存+断点续选
6. 子图切换不重建面板（`SubChartsMixin`）
