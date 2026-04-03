# 看盘页 MarketPage

**文件：** `app/pages/market_page.py` (~555 行)

## 页面定位

主力页面，左右分栏布局（QSplitter 1:3）。左侧为搜索筛选+选股面板，右侧为 StockChartWidget 四联图。

## 类结构

### UpdateWorker(QtCore.QObject)
后台线程 Worker，调用 `HistoryUpdater.update_all_symbols()` 批量更新股票日线。

**信号：** `progressChanged(dict)`, `finished(dict)`, `errorOccurred(str)`
**方法：** `run()`, `cancel()`

### ScreeningWorker(QtCore.QObject)
后台线程 Worker，调用 `ScreeningService.screen_with_cache()` 执行选股。

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
| `_chart_min_visible_days / _chart_max_visible_days` | 图表可见范围 |
| `_update_thread / _update_worker` | 更新任务线程与 Worker |
| `_screening_thread / _screening_worker` | 选股任务线程与 Worker |
| `_screening_results` | 选股命中结果列表 |
| `df_list` | 全量股票 DataFrame（含 `name_initials` 拼音首字母列） |
| `filtered` | 过滤后的股票 DataFrame |
| `screening_service` | ScreeningService 实例 |
| `template_service` | TemplateService 实例 |

## 公开方法

| 方法 | 说明 |
|------|------|
| `apply_settings(app_settings)` | 接收外部设置变更，更新图表范围和表单 |
| `reload_templates()` | 刷新选股条件模板下拉框（由 TemplatePage 触发） |
| `populate_table(df)` | 将 DataFrame 渲染到左侧股票列表表格 |
| `apply_filter(*_)` | 多条件过滤（搜索文本、行业）并刷新表格 |
| `on_select()` | 表格选中事件 → `_load_symbol()` |
| `run_screening()` | 启动选股后台任务 |
| `start_update_all()` | 启动全量更新后台任务 |
| `persist_page_state()` | 关闭时持久化最后选中股票代码 |

## 核心链路

```
搜索 → apply_filter() → on_select() → _load_symbol() → chart.set_daily() → onHover() → 状态栏
选股 → run_screening() → ScreeningWorker(线程) → populate_screening_results()
更新 → start_update_all() → UpdateWorker(线程) → 进度弹窗
```

## 左侧面板 UI 结构

1. 展开/收起设置面板（SettingsFormWidget）
2. 更新全部股票按钮
3. 搜索框：支持代码/名称/拼音首字母/行业/地区，空格分词多条件 AND
4. 行业下拉筛选
5. 选股面板：选择模板 + 日期 → 执行选股 → 选股结果表（6列）
6. 全部股票表（3列：代码、名称、行业）

## 模块依赖

- `SettingsService` — 加载/保存设置
- `ScreeningService` — 执行选股
- `TemplateService` — 获取/构造模板请求
- `HistoryUpdater` — 批量更新股票日线
- `StockChartWidget` — K线图表组件
- `SettingsFormWidget` — 可折叠设置面板
- `UpdateProgressDialog / ScreeningProgressDialog` — 进度弹窗
- `load_stock_list / load_daily_csv` — 数据加载
- `start_worker` — 通用线程启动工具
