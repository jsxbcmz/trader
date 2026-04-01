# StockViewer 项目上下文文档

更新时间：2026-03-31

## 项目定位

StockViewer 是一个基于 PySide6 + pyqtgraph 的本地股票日线桌面查看器，支持：
- 股票日线图表四联图展示（K线、成交额、砖型差值、KDJ）
- 通达信选股条件解析与智能选股
- 选股模板管理与批量选股
- 本地历史数据管理

## 技术栈

- GUI: PySide6 (Qt)
- 绑图: pyqtgraph
- 数据处理: pandas, numpy
- 中文搜索: pypinyin（可选）
- 数据源: Tushare API
- 打包: PyInstaller

## 启动方式

```bash
python -m run
# 或
python run.py
```

---

## 模块架构

### 一、应用层 (app/)

用户界面层，负责窗口、页面、交互逻辑。

#### 1.1 入口模块

| 文件 | 职责 |
|------|------|
| `main.py` | 应用入口，创建 QApplication 和 MainWindow |
| `main_window.py` | 主窗口，管理页面切换、菜单栏、状态栏 |

#### 1.2 页面模块 (app/pages/)

| 文件 | 职责 |
|------|------|
| `market_page.py` | **看盘页**：股票列表、搜索筛选、图表展示、选股执行、数据更新 |
| `template_page.py` | **模板页**：选股模板的 CRUD 管理 |
| `settings_page.py` | **设置页**：Tushare Token、图表范围等配置 |

#### 1.3 图表模块

| 文件 | 职责 |
|------|------|
| `widgets.py` | **核心**：StockChartWidget 四联图组件，十字线、浮窗、hover 联动 |
| `chart_layout.py` | 图表布局管理 |
| `chart_primitives.py` | 图元绘制（K线、砖型图等） |
| `chart_indicators.py` | 图表指标计算 |
| `chart_overlays.py` | 图表覆盖层（趋势线等） |
| `chart_ranges.py` | 范围控制逻辑 |
| `chart_interaction.py` | 鼠标交互处理 |

#### 1.4 对话框模块 (app/dialogs/)

| 文件 | 职责 |
|------|------|
| `template_editor_dialog.py` | 模板编辑对话框 |

#### 1.5 服务模块 (app/services/)

| 文件 | 职责 |
|------|------|
| `settings_service.py` | 应用设置持久化（QSettings） |

#### 1.6 数据模块

| 文件 | 职责 |
|------|------|
| `data_loader.py` | 股票列表 CSV 与日线 CSV 加载、字段标准化 |
| `history_updater.py` | 历史数据增量更新（Tushare API） |
| `tushare_client.py` | Tushare API 客户端封装 |

---

### 二、核心领域层 (core/)

业务逻辑核心，独立于 UI，可单独测试。

#### 2.1 数据访问 (core/data/)

| 文件 | 职责 |
|------|------|
| `repository.py` | StockRepository：股票日线数据访问 |
| `time_index.py` | 时间索引定位（按日期查找数据行） |

#### 2.2 表达式引擎 (core/expression/)

**通达信条件解析与执行的核心模块。**

| 文件 | 职责 |
|------|------|
| `parser/` | 词法分析、语法解析、AST 转换 |
| `nodes.py` | 表达式 AST 节点定义 |
| `evaluator.py` | 表达式求值引擎 |

**表达式节点类型：**
- `ConstantNode` - 常量
- `FieldNode` - 字段引用（OPEN, CLOSE, HIGH, LOW, VOLUME）
- `FunctionNode` - 函数调用（MA, REF, CROSS 等）
- `MathNode` - 数学运算（+, -, *, /）
- `ComparisonNode` - 比较运算（>, <, >=, <=, ==, !=）
- `LogicalNode` - 逻辑运算（AND, OR, NOT）

#### 2.3 技术指标 (core/indicators/)

| 文件 | 职责 |
|------|------|
| `registry.py` | 指标函数注册表 |
| `builtin.py` | 内置指标函数实现（MA, EMA, SMA, REF, CROSS 等） |
| `tdx_compat.py` | 通达信兼容性处理 |

**支持的指标函数：**
- 移动平均：MA, EMA, SMA, WMA
- 引用：REF, REFV
- 交叉：CROSS
- 统计：MAX, MIN, HHV, LLV, COUNT, SUM
- 逻辑：IF, AND, OR, NOT, EVERY, ANY, EXIST
- 数学：ABS, MAX, MIN, SQRT, LOG, POW

#### 2.4 领域模型 (core/models/)

| 文件 | 职责 |
|------|------|
| `template.py` | ScreeningTemplate：选股模板数据模型 |
| `screening.py` | ScreeningRequest/Result：选股请求与结果 |
| `stock_pool.py` | StockPool：股票池模型 |
| `market.py` | 股票基础信息模型 |
| `expression.py` | 表达式相关模型 |

#### 2.5 选股引擎 (core/screening/)

| 文件 | 职责 |
|------|------|
| `engine.py` | **ScreeningEngine**：选股执行引擎 |
| `service.py` | ScreeningService：选股服务门面 |
| `error_policy.py` | 错误处理策略 |
| `result_models.py` | 选股结果模型 |
| `result_formatter.py` | 结果格式化输出 |

**选股流程：**
```
模板 → 解析通达信代码 → AST → 遍历股票池 → 逐只求值 → 汇总结果
```

#### 2.6 股票池管理 (core/stock_pool/)

| 文件 | 职责 |
|------|------|
| `manager.py` | StockPoolManager：股票池管理，支持多池切换 |

#### 2.7 模板管理 (core/templates/)

| 文件 | 职责 |
|------|------|
| `service.py` | TemplateService：模板 CRUD 服务 |
| `repository.py` | TemplateRepository：模板 JSON 文件读写 |
| `builtin.py` | 内置默认模板 |

---

### 三、数据文件

| 路径 | 说明 |
|------|------|
| `stocklist.csv` | 股票列表（ts_code, symbol, name, area, industry） |
| `stock_daily_data/{symbol}.csv` | 个股日线数据（date, open, high, low, close, volume） |
| `templates.json` | 选股模板存储 |

---

## 核心交互链路

### 图表查看链路

```
搜索 → apply_filter() → on_select() → chart.set_daily() → onHover() → 状态栏更新
```

### 选股执行链路

```
选择模板 → 选择日期 → run_screening() → ScreeningEngine.run() → 显示结果
```

### 数据更新链路

```
点击更新 → UpdateWorker.run() → HistoryUpdater → TushareClient → 写入 CSV
```

---

## 修改定位规则

| 需求 | 优先查看 |
|------|----------|
| 改筛选/表格/状态栏 | `app/pages/market_page.py` |
| 改图表/十字线/tooltip/缩放 | `app/widgets.py` |
| 改通达信条件解析 | `core/expression/` |
| 改技术指标计算 | `core/indicators/` |
| 改选股逻辑 | `core/screening/engine.py` |
| 改模板管理 | `core/templates/service.py` |
| 改 CSV 加载/字段 | `app/data_loader.py` |
| 改数据更新/Tushare | `app/history_updater.py` |

---

## 稳定性原则

1. **切股时不重建 PlotWidget** - 只更新数据、日期映射和可视范围
2. **防重入标志** - `_loading_plot`、`_updating_range`
3. **hover 联动核心** - 十字线、浮窗、价格标签、状态栏、指标标签都围绕 `_on_mouse_moved()` 联动

---

## 数据约定

- `volume` 实际语义为**成交额（万元）**，展示时换算为**亿**
- `symbol` 需补齐为 6 位字符串
- `date` 转 datetime 并升序排列
- X 轴范围：最小 30 天，最大 150 天
