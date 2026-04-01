# StockViewer Quick Memory Card

更新时间：2026-03-31

## 项目定位
PySide6 + pyqtgraph 本地股票日线查看器，支持通达信选股条件智能选股。

## 模块速查

### 应用层 (app/)
| 模块 | 核心文件 | 职责 |
|------|----------|------|
| 入口 | `main.py` | 创建 QApplication |
| 主窗口 | `main_window.py` | 页面切换、菜单、状态栏 |
| 看盘页 | `pages/market_page.py` | 股票列表、图表、选股执行 |
| 模板页 | `pages/template_page.py` | 模板 CRUD |
| 设置页 | `pages/settings_page.py` | Token、图表配置 |
| 图表 | `widgets.py` | 四联图、hover联动 |
| 数据 | `data_loader.py` | CSV 加载 |

### 核心层 (core/)
| 模块 | 核心文件 | 职责 |
|------|----------|------|
| 表达式 | `expression/evaluator.py` | 通达信条件解析求值 |
| 指标 | `indicators/builtin.py` | MA/EMA/REF/CROSS 等指标 |
| 选股 | `screening/engine.py` | 选股执行引擎 |
| 模板 | `templates/service.py` | 模板管理服务 |
| 数据 | `data/repository.py` | 日线数据访问 |

## 核心链路

```
图表查看：搜索 → on_select → chart.set_daily → onHover → 状态栏
选股执行：模板 → ScreeningEngine → 遍历股票池 → 表达式求值 → 结果
```

## 修改定位

| 改什么 | 看哪里 |
|--------|--------|
| 筛选/表格/状态栏 | `app/pages/market_page.py` |
| 图表/十字线/tooltip | `app/widgets.py` |
| 通达信条件解析 | `core/expression/` |
| 技术指标计算 | `core/indicators/` |
| 选股逻辑 | `core/screening/engine.py` |
| 模板管理 | `core/templates/service.py` |

## 数据约定

- `volume` = 成交额（万元），展示换算为亿
- `symbol` 补齐 6 位
- X 轴范围：30~150 天

## 稳定性原则

1. 切股时不重建 PlotWidget
2. 防重入：`_loading_plot`、`_updating_range`
3. hover 联动是交互核心
