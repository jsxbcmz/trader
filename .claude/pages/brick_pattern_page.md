# 定式验证页 BrickPatternPage

**文件：** `app/pages/brick_pattern_page.py` (~561 行)

## 页面定位

砖形图交易定式批量验证页面。预填充文档中的案例数据，支持批量验证和手动添加，展示每条数据的验证结论（是否符合定式、符合哪个、匹配度）。

## 类结构

### BrickPatternPage(QtWidgets.QWidget)

**信号：** `statusMessageRequested = Signal(str, int)`
**构造参数：** `root: Path`

## 关键状态变量

| 变量 | 说明 |
|------|------|
| `root` | 项目根目录 |
| `repository` | StockRepository 实例 |
| `_verify_index` | 批量验证当前行索引 |
| `_verify_stats` | 验证统计 `{pass, fail, risk, error}` |

## 结果表列

| 列 | 说明 |
|----|------|
| 代码 | 股票代码（6位） |
| 日期 | 目标日期（YYYY-MM-DD） |
| 期望定式 | 人工标注的预期定式类型 |
| 前提 | 必备前提检测结果（✅/❌/⛔） |
| 匹配定式 | 引擎匹配到的定式类型 |
| 匹配度 | 匹配评分 |
| 风险 | 风险过滤结果 |
| 详情 | 详细描述信息 |

## 默认案例数据

内置 `DEFAULT_CASES` 包含 16 条测试案例：
- N型起跳：5 条
- 横盘起跳：7 条
- 上升波段延续：4 条

## UI 结构

1. 顶部标题栏 + 手动添加区域（代码/日期/期望定式输入 + 添加按钮）
2. 操作按钮栏：批量验证全部 / 重置为默认 / 清空结果 / 删除选中行 + 统计标签
3. 结果表格（8列，支持行选中、双击弹窗查看图表）

## 核心方法

| 方法 | 说明 |
|------|------|
| `_load_default_cases()` | 加载默认案例到表格 |
| `_on_add_case()` | 手动添加一条验证数据 |
| `_on_verify_all()` | 启动批量验证（用 QTimer 逐行，避免阻塞 UI） |
| `_verify_next_row()` | 验证当前行并调度下一行 |
| `_execute_single_verify(row, code, date)` | 单行验证：加载数据 → 前提检测 → 定式检测 → 风险过滤 |
| `_on_row_double_clicked(index)` | 双击弹出图表弹窗 |
| `_set_row_result(...)` | 填充结果列（含行背景色、匹配列颜色） |
| `_set_row_error(row, message)` | 填充错误信息 |
| `_update_stats()` | 更新统计标签 |

## 验证流程

```
批量验证 → QTimer 逐行调度 → _execute_single_verify()
  → StockRepository.get_daily_frame()
  → locate_time_index()
  → _calc_indicators()
  → check_prerequisites()        [前提检测]
  → detect_n_shape_jump()         [N型起跳]
  → detect_sideways_jump()        [横盘起跳]
  → detect_uptrend_continue()     [上升波段延续]
  → filter_limit_down/long_sideways/heavy_volume_drop()  [风险过滤]
  → 填充结果列 + 统计
```

## 行背景色规则

| 颜色 | 含义 |
|------|------|
| `#F6FFED` 浅绿 | 匹配通过且无风险 |
| `#FFFBE6` 浅黄 | 匹配但有风险 |
| `#FFF1F0` 浅红 | 前提不满足或无匹配 |
| `#FFF2E8` 浅橙 | 数据错误 |

## 模块依赖

- `StockRepository` — 股票数据访问
- `locate_time_index` — 日期定位
- `_calc_indicators` — 指标计算
- `check_prerequisites` — 前提检测
- `detect_n_shape_jump / detect_sideways_jump / detect_uptrend_continue` — 三种定式检测
- `filter_limit_down / filter_long_sideways / filter_heavy_volume_drop` — 风险过滤
- `StockChartWidget` — 双击弹窗图表
- `load_daily_csv` — 数据加载
