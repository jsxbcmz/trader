# StockViewer Quick Memory Card

更新时间：2026-03-29

- 项目类型：PySide6 + pyqtgraph 的本地股票日线查看器（桌面 GUI）
- 主要结构：左侧股票搜索/行业筛选/表格，右侧四联图（K线主图 + 成交额 + 砖型差值 + KDJ）
- 核心文件：
  - `app/main.py`：主窗口、筛选、选股、状态栏、恢复上次选股
  - `app/widgets.py`：四联图、十字线、tooltip、价格标签、指标标签、范围联动
  - `app/data_loader.py`：股票列表与日线 CSV 加载
- 入口：`python -m run` 或 `python run.py`
- 数据约定：
  - `stocklist.csv` 需要 `ts_code, symbol, name, area, industry`
  - `stock_daily_data/{symbol}.csv` 需要 `date, open, close, high, low, volume`
  - `symbol` 补齐 6 位，`date` 转 datetime 并升序
  - `volume` 在本项目中实际表示成交额（万元），展示通常换算为亿
- 主交互链路：`apply_filter -> on_select -> chart.set_daily -> onHover -> MainWindow.on_hover`
- hover 是交互核心：十字线、浮窗、价格标签、状态栏、指标标签都围绕 `_on_mouse_moved()` 联动
- 图表稳定性铁律：切换股票时不要重建 `PlotWidget`，只更新数据、日期映射和可视范围
- 防重入标志：重点关注 `_loading_plot`、`_updating_range`
- 改动定位规则：
  - 改筛选/表格/状态栏 -> `main.py`
  - 改图表/十字线/tooltip/价格标签/缩放/指标 -> `widgets.py`
  - 改 CSV 兼容/字段清洗 -> `data_loader.py`
- 非必要不优先看：`build/`, `dist/`
