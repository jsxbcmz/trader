# StockViewer Quick Memory Card

更新时间：2026-04-02

## 项目定位
PySide6 + pyqtgraph 本地股票日线查看器，支持通达信选股、模拟交易训练。

## 四个页面
| 页面 | 文件 | 核心功能 |
|------|------|----------|
| 看盘页 | `pages/market_page.py` | 股票搜索、四联图、快速选股、数据更新 |
| 模板页 | `pages/template_page.py` | 模板 CRUD |
| 设置页 | `pages/settings_page.py` | Token、图表配置 |
| 选股页 | `pages/screening_page.py` | 两态(配置→结果)、模拟交易(T+1) |

## 四联图指标
| 面板 | 占比 | 计算 |
|------|------|------|
| K线 | 3/6 | 蜡烛图 + 趋势EMA(EMA(C,10),10) + 多空MA均值 |
| 成交额 | 1/6 | 红绿柱(亿) |
| 砖型差值 | 1/6 | HHV/LLV(4)+SMA多步 |
| KDJ | 1/6 | RSV→SMA→K,D,J=3K-2D |

## 核心层速查
| 模块 | 核心文件 | 职责 |
|------|----------|------|
| 表达式 | `core/expression/` | 通达信条件解析求值 |
| 指标 | `core/indicators/builtin.py` | MA/EMA/REF/CROSS 等 |
| 选股 | `core/screening/engine.py` | 选股引擎 |
| 缓存 | `core/screening/cache_*.py` | 选股缓存+断点续选 |
| 模板 | `core/templates/service.py` | 模板服务 |
| 交易 | `core/trade/simulator.py` | 模拟买卖/持仓/结算 |

## 修改定位
| 改什么 | 看哪里 |
|--------|--------|
| 看盘页 | `app/pages/market_page.py` |
| 选股页/模拟交易 | `app/pages/screening_page.py` |
| 四联图/hover | `app/widgets.py` |
| 图表指标计算 | `app/chart_indicators.py` |
| K线/砖型图元 | `app/chart_primitives.py` |
| 通达信解析 | `core/expression/` |
| 选股引擎 | `core/screening/engine.py` |
| 模拟交易逻辑 | `core/trade/simulator.py` |
| 线程管理 | `app/utils/thread_manager.py` |

## 数据约定
- `volume` = 成交额(万元)，展示亿
- 看盘页X轴：30~150天；选股页：固定90天
- `symbol` 补齐6位

## 稳定性原则
1. 切股不重建 PlotWidget
2. 防重入：`_loading_plot`、`_updating_range`
3. hover 联动是交互核心
4. `start_worker()` 统一线程管理
5. `screen_with_cache()` 缓存+断点续选
