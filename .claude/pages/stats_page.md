# 统计页 StatsPage

**文件：** `app/pages/stats_page.py` (~1309 行)

## 页面定位

数据采集与持仓分析页面。从外部 API 采集比赛排名和每日持仓数据，展示持仓操作分析、收益详情图表。

## 类结构

### CollectWorker(QtCore.QObject)
后台采集任务 Worker，按配置顺序请求多个 API 接口。

**信号：**
- `logMessage(str, str)` — (message, level)
- `apiStart(str, int)` — (api_id, total)
- `apiProgress(str, int, int, float)` — (api_id, current, total, elapsed)
- `apiDone(str, int, float)` — (api_id, count, elapsed)
- `apiCached(str)` — (api_id,) 缓存命中
- `apiError(str)` — (api_id,)
- `allDone(str)` — (report_text,)

### ApiCard(QtWidgets.QFrame)
单个接口的进度卡片 Widget，显示接口名称、进度条、状态标签。

### OperationTag(QtWidgets.QPushButton)
可点击的操作筛选标签（建仓/加仓/减仓/清仓/T操作等），支持激活态切换。

### PositionsTable(QtWidgets.QTableWidget)
持仓操作数据表格，支持列排序、双击跳转图表、收益详情按钮。

**信号：**
- `stockDoubleClicked(str, str)` — (code, name)
- `rateDetailRequested(str, str, str)` — (code, op, name)

### StatsPage(QtWidgets.QWidget)

**信号：** `statusMessageRequested = Signal(str, int)`
**构造参数：** `root: Path`

## 关键状态变量

| 变量 | 说明 |
|------|------|
| `_collect_thread / _collect_worker` | 采集任务线程与 Worker |
| `_positions_data` | 原始持仓数据列表 |
| `_filtered_data` | 筛选后的持仓数据 |
| `_active_filter` | 当前激活的操作筛选标签 |
| `_api_cards` | API 进度卡片字典 |

## 接口配置

| 接口 ID | 显示名称 | 说明 |
|---------|----------|------|
| `api1` | 📋 比赛排名 | 比赛排名数据 |
| `api2` | 📈 每日持仓 | 每日持仓操作数据 |

## 操作类型映射

| 代码 | 标签 | 颜色 |
|------|------|------|
| 0 | 不变 | 灰色 |
| 1 | 加仓 | 绿色 |
| 2 | 减仓 | 黄色 |
| 3 | 建仓 | 亮绿 |
| 4 | 清仓 | 红色 |
| 7 | 大幅加仓 | 深绿 |
| 8 | 大幅减仓 | 橙色 |
| 9 | T操作 | 蓝色 |

## UI 结构

1. 顶部操作栏：采集按钮 + 搜索框 + 操作筛选标签流
2. API 进度卡片区（采集时显示）
3. 持仓数据表格（代码、名称、操作、持有人数、收益详情按钮）
4. 收益详情弹窗：StockChartWidget K线图 + 收益率标注

## 核心方法

| 方法 | 说明 |
|------|------|
| `_start_collect()` | 启动采集 Worker |
| `_on_collect_done(report)` | 采集完成 → 加载数据 → 渲染表格 |
| `_load_positions_data()` | 从 output/day_positions.json 加载持仓数据 |
| `_apply_filter(op_code)` | 按操作类型筛选 |
| `_on_search_changed(text)` | 按代码/名称/拼音搜索 |
| `_on_stock_double_clicked(code, name)` | 双击股票 → 弹出图表 |
| `_on_rate_detail(code, op, name)` | 收益详情 → 弹出图表+收益标注 |

## 数据流

```
采集 → CollectWorker → ApiRequester → DataStorage(output/*.json)
展示 → _load_positions_data() → PositionsTable → 筛选/搜索/排序
详情 → StockChartWidget + load_daily_csv → 收益率计算 + 图表标注
```

## 模块依赖

- `app/stats/config_loader.py` — API 配置加载（读取 `app/stats/api_config.json`）
- `app/stats/requester.py` — HTTP 接口请求引擎（分页/批量/单次）
- `app/stats/storage.py` — 数据持久化（JSON 文件 + 当天缓存校验）
- `app/stats/analyzer.py` — 汇总分析报告
- `StockChartWidget` — K线图表
- `load_daily_csv / get_last_trade_date` — 数据加载
- `HistoryUpdater` — 按需更新单只股票数据
- `start_worker` — 后台线程
