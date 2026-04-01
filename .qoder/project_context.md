# StockViewer 项目上下文

更新时间：2026-03-31

## 项目概述

StockViewer 是一个基于 PySide6 + pyqtgraph 的本地股票日线桌面查看器，支持通达信选股条件智能选股。

**技术栈：** PySide6 + pyqtgraph + pandas + numpy + Tushare API

**入口：** `python -m run` 或 `python run.py`

---

## 模块架构

### 应用层 (app/)

用户界面层，负责窗口、页面、交互逻辑。

```
app/
├── main.py                    # 应用入口
├── main_window.py             # 主窗口，页面切换
├── pages/
│   ├── market_page.py         # 看盘页：股票列表、图表、选股
│   ├── template_page.py       # 模板页：模板 CRUD
│   └── settings_page.py       # 设置页：Token、图表配置
├── dialogs/
│   └── template_editor_dialog.py
├── services/
│   └── settings_service.py
├── widgets.py                 # 四联图组件（核心）
├── chart_*.py                 # 图表模块
├── data_loader.py             # CSV 加载
├── history_updater.py         # 历史数据更新
└── tushare_client.py          # Tushare API
```

### 核心层 (core/)

业务逻辑核心，独立于 UI。

```
core/
├── data/
│   ├── repository.py          # 股票数据访问
│   └── time_index.py          # 时间索引定位
├── expression/
│   ├── parser/                # 词法/语法解析
│   ├── nodes.py               # AST 节点
│   └── evaluator.py           # 表达式求值
├── indicators/
│   ├── registry.py            # 指标注册表
│   ├── builtin.py             # 内置指标实现
│   └── tdx_compat.py          # 通达信兼容
├── models/
│   ├── template.py            # 选股模板
│   ├── screening.py           # 选股请求/结果
│   └── stock_pool.py          # 股票池
├── screening/
│   ├── engine.py              # 选股引擎（核心）
│   ├── service.py             # 选股服务
│   └── result_models.py       # 结果模型
├── stock_pool/
│   └── manager.py             # 股票池管理
└── templates/
    ├── service.py             # 模板服务
    └── repository.py          # 模板存储
```

---

## 核心交互链路

### 图表查看
```
搜索 → apply_filter → on_select → chart.set_daily → onHover → 状态栏
```

### 选股执行
```
模板 → ScreeningEngine → 解析通达信代码 → 遍历股票池 → 表达式求值 → 结果
```

---

## 修改定位规则

| 改什么 | 看哪里 |
|--------|--------|
| 筛选/表格/状态栏 | `app/pages/market_page.py` |
| 图表/十字线/tooltip | `app/widgets.py` |
| 通达信条件解析 | `core/expression/` |
| 技术指标计算 | `core/indicators/builtin.py` |
| 选股逻辑 | `core/screening/engine.py` |
| 模板管理 | `core/templates/service.py` |

---

## 数据约定

- `stocklist.csv`: ts_code, symbol, name, area, industry
- `stock_daily_data/{symbol}.csv`: date, open, high, low, close, volume
- `volume` = 成交额（万元），展示换算为亿
- `symbol` 补齐 6 位
- X 轴范围：最小 30 天，最大 150 天

---

## 稳定性原则

1. 切股时不重建 PlotWidget，只更新数据
2. 防重入标志：`_loading_plot`、`_updating_range`
3. hover 联动是交互核心
