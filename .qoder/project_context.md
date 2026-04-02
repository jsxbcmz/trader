# StockViewer 项目上下文

更新时间：2026-04-02

## 项目概述

StockViewer 是一个基于 PySide6 + pyqtgraph 的本地股票日线桌面查看器，支持通达信选股条件智能选股和模拟交易训练。

**技术栈：** PySide6 + pyqtgraph + pandas + numpy + numba(可选) + Tushare API

**入口：** `python -m run` 或 `python run.py`

---

## 四个主要页面

| 页面 | 文件 | 职责 |
|------|------|------|
| 看盘页 | `app/pages/market_page.py` | 股票列表搜索、四联图查看、快速选股、数据更新 |
| 模板页 | `app/pages/template_page.py` | 选股模板 CRUD 管理 |
| 设置页 | `app/pages/settings_page.py` | Token 配置、图表参数、触发批量更新 |
| 选股页 | `app/pages/screening_page.py` | 两态设计（配置→结果），含模拟交易（T+1） |

## 四联图指标

四联图 (`app/widgets.py`) 四个面板 X 轴联动，hover 同步更新所有面板。

| 面板 | 占比 | 内容 | 计算 |
|------|------|------|------|
| K线图 | 3/6 | 蜡烛图 + 知行短期趋势线(白) + 知行多空线(金) | 趋势=EMA(EMA(C,10),10), 多空=MA(14,28,57,114)均值 |
| 成交额 | 1/6 | 红绿柱状图(亿元) | volume÷10000 |
| 砖型差值 | 1/6 | 红绿差值柱 + 零线 | HHV/LLV(4) + SMA 多步计算 |
| KDJ | 1/6 | K(白)/D(金)/J(紫) + 20/50/80参考线 | RSV→SMA(3,1)→K,D,J=3K-2D |

---

## 模块架构

### 应用层 (app/)

```
app/
├── main.py / main_window.py   # 入口 + 主窗口(4页面)
├── pages/                     # 4个页面
├── components/settings_form.py # 设置表单复用组件
├── dialogs/                   # 模板编辑弹窗
├── services/settings_service.py
├── utils/thread_manager.py    # start_worker 线程管理
├── widgets.py                 # StockChartWidget 四联图 + 进度弹窗
├── chart_layout.py            # 布局 dataclass + 工厂
├── chart_primitives.py        # K线/砖型图图元
├── chart_indicators.py        # EMA/MA/KDJ/Brick 计算
├── chart_overlays.py          # HTML 标签构建
├── chart_ranges.py            # X轴 clamp
├── chart_interaction.py       # ViewBox 鼠标交互
├── data_loader.py / history_updater.py / tushare_client.py
```

### 核心层 (core/)

```
core/
├── data/                      # 数据访问 + JSON基类 + 时间索引
├── expression/                # 通达信条件解析(词法/语法/AST/求值)
├── indicators/                # 指标注册表 + 内置实现
├── models/                    # 模板/选股/交易/股票池 模型
├── screening/                 # 选股引擎 + 服务 + 缓存
├── stock_pool/                # 股票池管理
├── templates/                 # 模板服务 + 存储
├── trade/simulator.py         # 模拟交易(买卖/持仓/结算)
└── utils/                     # 日期/字符串工具
```

---

## 修改定位规则

| 改什么 | 看哪里 |
|--------|--------|
| 看盘页筛选/表格/状态栏 | `app/pages/market_page.py` |
| 选股页/模拟交易 | `app/pages/screening_page.py` |
| 模板管理界面 | `app/pages/template_page.py` |
| 四联图/hover/十字线 | `app/widgets.py` |
| 图表布局/面板结构 | `app/chart_layout.py` |
| K线/砖型图图元 | `app/chart_primitives.py` |
| 指标计算(EMA/KDJ/Brick) | `app/chart_indicators.py` |
| 信息浮窗/标签HTML | `app/chart_overlays.py` |
| 通达信条件解析 | `core/expression/` |
| 选股引擎 | `core/screening/engine.py` |
| 选股缓存 | `core/screening/cache_*.py` |
| 模拟交易逻辑 | `core/trade/simulator.py` |
| 模板服务 | `core/templates/service.py` |
| 线程管理 | `app/utils/thread_manager.py` |

---

## 数据约定

- `volume` = 成交额（万元），展示换算为亿
- `symbol` 补齐 6 位，`date` 转 datetime 升序
- 看盘页 X 轴：30~150 天（可配置）
- 选股页 X 轴：固定 90 天，禁用拖动缩放

## 稳定性原则

1. 切股不重建 PlotWidget，只更新数据
2. 防重入：`_loading_plot`、`_updating_range`
3. hover 联动是交互核心
4. 线程互斥：`start_worker()` 统一管理
5. 选股缓存：`screen_with_cache()` 支持缓存命中和断点续选
