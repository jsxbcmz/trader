# 看盘页 MarketPage

**文件：** `app/pages/market_page.py` (~365 行)

## 页面定位

主力页面，左右分栏布局（QSplitter 1:3）。左侧为搜索+股票列表+子图选择器，右侧为 StockChartWidget 图表。

## 类结构

### UpdateWorker(QtCore.QObject)
后台线程 Worker，调用 `HistoryUpdater.update_all_symbols()` 批量更新股票日线。

**信号：** `progressChanged(dict)`, `finished(dict)`, `errorOccurred(str)`
**方法：** `run()`, `cancel()`

### MarketPage(QtWidgets.QWidget)

**信号：**
- `statusMessageRequested = Signal(str, int)` — 向主窗口状态栏发消息
- `updateRunningChanged = Signal(bool)` — 通知更新任务状态变更

**构造参数：** `root: Path, settings_service: SettingsService`

## 关键状态变量

| 变量 | 说明 |
|------|------|
| `_last_selected_symbol` | 最后选中股票代码（持久化到 QSettings） |
| `_tushare_token` | Tushare Token |
| `_chart_min_visible_days / _chart_max_visible_days` | 图表可见范围 |
| `_update_thread / _update_worker` | 更新任务线程与 Worker |
| `df_list` | 全量股票 DataFrame（含 `name_initials` 拼音首字母列） |
| `filtered` | 过滤后的股票 DataFrame |

## 公开方法

| 方法 | 说明 |
|------|------|
| `apply_settings(app_settings)` | 接收外部设置变更，更新图表范围 |
| `populate_table(df)` | 将 DataFrame 渲染到左侧股票列表表格 |
| `apply_filter(*_)` | 搜索文本过滤并刷新表格 |
| `on_select()` | 表格选中事件 → `_load_symbol()` |
| `start_update_all()` | 启动全量更新后台任务 |
| `persist_page_state()` | 关闭时持久化最后选中股票代码 |

## 子图选择器

通过 `SubChartSelector` 组件，用户可动态切换图表可见子图（成交额/砖型差值/KDJ/单针下20/MACD）。

| 方法 | 说明 |
|------|------|
| `_on_sub_chart_changed(selected)` | 子图选择变化回调，更新图表并持久化选择 |
| `_load_sub_chart_selection()` | 从 QSettings 加载持久化的子图选择 |

## 核心链路

```
搜索 → apply_filter() → on_select() → _load_symbol() → chart.set_daily() → onHover() → 状态栏
更新 → start_update_all() → UpdateWorker(线程) → 进度弹窗
子图切换 → SubChartSelector → _on_sub_chart_changed() → chart.set_visible_sub_charts()
```

## 左侧面板 UI 结构

1. 更新全部股票按钮 + 子图选择器按钮
2. 搜索框：支持代码/名称/拼音首字母/行业/地区，空格分词多条件 AND
3. 全部股票表（3列：代码、名称、行业）

## 模块依赖

- `SettingsService` — 加载/保存设置
- `HistoryUpdater` — 批量更新股票日线
- `TushareClient` — 数据源客户端
- `StockChartWidget` — K线图表组件
- `SubChartSelector` — 子图选择下拉按钮
- `SubChartType` — 子图类型枚举
- `UpdateProgressDialog` — 进度弹窗
- `load_stock_list / load_daily_csv` — 数据加载
- `start_worker` — 通用线程启动工具
- `pypinyin`（可选）— 拼音首字母搜索
