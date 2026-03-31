# StockViewer Claude Project Context

更新时间：2026-03-29

## Ultra-Short Memory Card
- PySide6 + pyqtgraph 的本地股票日线查看器
- 左侧：搜索 / 行业筛选 / 股票表格
- 右侧：四联图（K线主图、成交额、砖型差值、KDJ）
- 主文件：`app/main.py`、`app/widgets.py`、`app/data_loader.py`
- 搜索支持：代码、名称、拼音首字母、ts_code、行业、地区
- `volume` 实际是成交额（万元），展示常换算为亿
- `main.py` 管列表/筛选/状态栏/恢复上次选股
- `widgets.py` 管多图联动、hover、指标、缩放范围
- 最重要稳定性原则：**切股时不要重建 plot**
- 防重入关键：`_loading_plot`、`_updating_range`

## 一、稳定事实（长期有效）

### 项目定位
- 本项目是一个本地股票日线查看器（GUI 桌面应用）。
- 左侧提供股票列表搜索、行业筛选、股票表格；右侧提供多指标联动图表。
- 当前右侧已形成四联图结构：
  1. 价格主图（K 线 + 自定义趋势线）
  2. 成交额副图
  3. 砖型差值图
  4. KDJ 副图
- 支持 hover 联动、十字光标、主图浮窗、价格标签、状态栏信息同步更新。

### 技术栈
- GUI: PySide6 (Qt)
- 绘图: pyqtgraph
- 数据处理: pandas, numpy
- 中文搜索增强: pypinyin（可选，用于名称拼音首字母搜索）
- 打包: PyInstaller

### 目录与核心文件
- `run.py`: 程序入口，转发到 `app.main.main()`
- `app/main.py`: 主窗口、左侧筛选列表、股票选择、状态栏更新、上次选股恢复
- `app/data_loader.py`: 股票列表 CSV 与日线 CSV 读取、字段标准化
- `app/widgets.py`: 图表组件，包含 K 线、自定义指标、成交额、砖型图、KDJ、十字线、浮窗、价格标签、范围联动
- `stocklist.csv`: 股票列表
- `stock_daily_data/{symbol}.csv`: 个股日线数据
- `build/`, `dist/`: 打包产物，除非涉及打包问题，否则通常不需要优先阅读

### 启动方式
开发态通常使用：
- `python -m run`
- 或 `python run.py`

路径逻辑：
- frozen 模式：root 为可执行文件所在目录
- dev 模式：root 为 `main.py` 上一级目录（项目根目录）

### 数据约定
#### 股票列表 `stocklist.csv`
期望至少包含列：
- `ts_code, symbol, name, area, industry`

处理约定：
- `symbol` 需要补齐为 6 位字符串（`zfill(6)`）
- `main.py` 会进一步生成 `name_initials` 列，用于名称拼音首字母搜索
- 主窗口初始化时会过滤掉本地 `stock_daily_data/` 目录中没有对应 CSV 的股票

#### 日线数据 `stock_daily_data/{symbol}.csv`
期望至少包含列：
- `date, open, close, high, low, volume`

处理约定：
- `date` 转为 datetime
- 按 `date` 升序排序
- 标准列顺序整理为：`date, open, high, low, close, volume`
- OHLC / volume 统一转数值
- 丢弃 OHLC 缺失行
- 注意：`volume` 在本项目中实际语义为“成交额（万元）”，显示时通常换算为“亿”（`volume / 1e4`）

### 主窗口交互链路
主窗口职责集中在 `app/main.py`：
- 初始化窗口和 root 路径
- 加载股票列表
- 过滤出本地 `stock_daily_data/` 中实际存在 CSV 的股票
- 构建左侧搜索框、行业下拉框、股票表格
- 构建右侧 `StockChartWidget`
- 绑定信号与槽
- 使用 `QSettings("StockViewer", "StockViewer")` 记住并恢复上次选中的股票代码

典型交互链路：
1. 搜索词变化 / 行业变化 -> `apply_filter()`
2. 表格选择变化 / 双击 -> `on_select()`
3. `on_select()` 加载对应股票日线数据 -> `chart.set_daily(df)`
4. 图表 hover -> `chart.onHover` -> `MainWindow.on_hover()`
5. 状态栏展示日期、OHLC、成交额(亿)
6. 窗口关闭 -> 保存 `last_selected_symbol`

搜索约定：
- 搜索框支持空格分词 AND 匹配（每个词都需要命中）
- 支持匹配字段：`symbol`、`name`、`name_initials`、`ts_code`、`industry`、`area`

### 关键稳定性规则（高优先级）
这些规则在后续修改中应优先遵守：

1. 不要在切换股票时重建 PlotWidget
- `pricePlot` / `volPlot` / `brickPlot` / `kdjPlot` 应只在 `__init__` 中创建一次
- 切换股票时应只更新：
  - 数据本身
  - 日期轴映射
  - 初始可视范围
  - 各 plot 中的 item 数据

2. 避免 scene / signal / item 生命周期混乱
- 多图联动依赖 scene 信号、SignalProxy、交叉引用和共享 range
- 如果切股时重建 plot，容易导致旧 scene / 新 scene、旧 signal / 新 signal 混用，鼠标快速移动时易出现显示异常甚至崩溃

3. 使用保护标志避免重入
- `_loading_plot`: 在切换股票、更新图表数据期间屏蔽 hover 逻辑
- `_updating_range`: 在范围更新过程中防止 `sigRangeChanged` 回调重入

4. `clear()` 后记得恢复关键 item
- 当前 `set_daily()` 中会对 `volPlot` / `brickPlot` / `kdjPlot` 调用 `clear()`
- 清空后需要重新挂回：
  - 竖线
  - 参考线
  - 曲线
  - 自定义图元
否则 hover 或显示逻辑会失效

## 二、易变实现细节（代码变动后优先核对）

### 图表组件职责
图表核心在 `app/widgets.py` 中的 `StockChartWidget`。

### 当前图表结构
共有四个联动子图：
- `pricePlot`: 价格主图（K 线 + 自定义趋势线）
- `volPlot`: 成交额副图（柱状图）
- `brickPlot`: 砖型差值图
- `kdjPlot`: KDJ 副图

X 轴联动：
- `pricePlot` 与 `volPlot` / `brickPlot` / `kdjPlot` 共用日期索引范围
- 四个子图都使用 `DateAxisItem` 把 x 索引映射为日期字符串

### 主要自定义类
- `DateAxisItem`: 日期轴，把整数 x 索引映射为 `YYYY-MM-DD`
- `CandlestickItem`: 自绘 K 线图元
- `BrickDeltaItem`: 自绘砖型差值矩形段图元
- `StockChartViewBox`: 自定义 ViewBox，用于限制滚轮缩放边界
- `StockChartWidget`: 总控件，负责数据装载、图形更新、hover 联动、范围控制

### 主图元素
- `candleItem`: K 线
- `zx_short_trend`: 知行短期趋势线
- `zx_long_short`: 知行多空线
- `vLine`, `hLine`: 主图十字线
- `infoText`: 主图跟随浮窗
- `yValueText`: 横向辅助线价格标签
- `indicatorLabel`: 主图左上角指标值标签

### 成交额图元素
- 成交额柱状图（红涨绿跌）
- `volVLine`: 同步竖线

### 砖型图元素
- `brickDeltaItem`: 砖型差值图元
- `brickZeroLine`: 0 轴参考线
- `brickVLine`: 同步竖线
- `brickDeltaLabel`: 左上角差值标签

### KDJ 图元素
- `kdjKCurve`, `kdjDCurve`, `kdjJCurve`: K / D / J 曲线
- `kdjLowLine`, `kdjMidLine`, `kdjHighLine`: 20 / 50 / 80 参考线
- `kdjVLine`: 同步竖线
- `kdjLabel`: 左上角指标标签

### 指标与计算逻辑
#### 主图指标
- `知行短期趋势线`：使用双重 EMA(10)
- `知行多空线`：对多个周期均线求均值，周期为 `14, 28, 57, 114`

#### 砖型差值指标
通过以下方法组合计算：
- 滚动最高值 `_rolling_max`
- 滚动最低值 `_rolling_min`
- TDX 风格平滑 `_tdx_sma`
- 多步中间变量计算得到 `brick`
- 最终以矩形 stickline 形式绘制相邻砖值差段

#### KDJ 指标
- 使用 9 日 RSV
- `K = SMA(RSV, 3, 1)`
- `D = SMA(K, 3, 1)`
- `J = 3K - 2D`

### hover 与状态联动
hover 是当前图表交互核心，核心处理入口是：
- `_on_mouse_moved()`
- `_update_hover_for_index()`

每次 hover 通常会：
1. 将 x 吸附到最近的整数索引
2. 同步更新四个子图中的竖线位置
3. 如果鼠标位于主图，则更新主图横线、浮窗、价格标签
4. 更新主图指标标签、砖型标签、KDJ 标签
5. 发射 `onHover.emit(dict)` 给主窗口更新状态栏

hover dict 当前包含：
- `index`
- `date`
- `open`
- `high`
- `low`
- `close`
- `preclose`
- `pct_chg`
- `amount_yi`

鼠标离开所有子图或越界时：
- 主图浮窗隐藏
- 价格标签隐藏
- hover 索引缓存清空

### UI/UX 约定
当前图表交互风格包括：
- 主图浮窗跟随鼠标，而不是固定在左上角
- 浮窗会根据鼠标位置自动翻转，尽量避免遮挡当前数据
- 浮窗内容通常展示：日期、收盘价、涨跌幅、成交额（亿）
- 浮窗颜色与涨跌联动：上涨偏红、下跌偏绿、平盘偏灰
- 横向辅助线价格标签会根据鼠标横向位置自动切换显示在左侧或右侧
- 主图、成交额图、砖型图、KDJ 图都通过共享索引实现联动查看
- 各副图左上角会显示当前 hover 对应的指标值

### 范围联动与缩放约定
#### X 轴范围
- 四个子图共享 X 轴可视范围
- 当前最小可视天数：`30`
- 当前最大可视天数：`150`
- 右侧保留额外可视 padding：`1.5`

相关函数：
- `_window_width_for_days`
- `_apply_xrange_limits`
- `_clamp_xrange`
- `StockChartViewBox.wheelEvent`

#### Y 轴范围
主图 Y 轴不完全依赖默认 auto-range，而是根据当前可见 x 范围内的数据动态计算：
- 可见区间 `low.min()`
- 可见区间 `high.max()`
- 再手动 `setYRange(...)`
- 上下边距约 4%

副图：
- 成交额图默认启用 y 自动范围
- 砖型图根据当前砖型数据手动设置 y 范围
- KDJ 图根据 K/D/J 数据手动设置 y 范围，并至少覆盖 0~100 区间

## 修改代码时的默认判断规则
当用户让我修改此项目时，优先按下面思路定位：

### 改左侧筛选 / 表格 / 状态栏 / 默认选股恢复
优先看：
- `app/main.py`

### 改 K 线 / 趋势线 / 成交额柱 / 十字线 / tooltip / 价格标签 / 四联图 hover
优先看：
- `app/widgets.py`

### 改拼音搜索 / 股票列表读取 / 日线 CSV 兼容 / 字段清洗
优先看：
- `app/main.py`
- `app/data_loader.py`

### 改启动逻辑 / 路径推导 / 打包后资源定位
优先看：
- `run.py`
- `app/main.py`
- `StockViewer.spec`

## 修改时的检查清单
每次修改前，默认检查：
1. 这次改动属于 `main.py`、`widgets.py`、还是 `data_loader.py`？
2. 是否影响 `on_select -> set_daily -> hover/status bar` 这条主链路？
3. 是否会破坏“切股时不重建 PlotWidget”的稳定性规则？
4. 是否涉及成交额单位换算（万元 -> 亿）？
5. 是否需要同时同步 tooltip、状态栏、overlay 标签三处显示？
6. 是否会导致 rangeChanged / mouseMoved 重入或越界问题？
7. 是否影响四联图之间的 X 轴联动和 hover 联动？

## 回答问题时的默认策略
后续若用户提问比较简短，可优先按以下策略理解：
- “图表 / 十字线 / 浮窗 / 标签 / 崩溃 / 缩放 / 指标 / hover” -> 优先从 `app/widgets.py` 分析
- “筛选 / 表格 / 状态栏 / 选股 / 默认加载 / 记住上次选择 / 拼音搜索” -> 优先从 `app/main.py` 分析
- “CSV / 字段 / 格式 / 加载失败” -> 优先从 `app/data_loader.py` 分析
- “打包 / 路径 / 运行入口” -> 优先从 `run.py` / `app/main.py` / `StockViewer.spec` 分析

## 不建议默认关注的内容
除非用户明确提到，否则不要优先把时间花在：
- `build/`
- `dist/`
- PyInstaller 生成的中间分析文件
- `__pycache__/`

## Quick Memory Card
- 一个 **PySide6 + pyqtgraph** 的本地股票日线桌面查看器
- 左侧是 **搜索 / 行业筛选 / 股票表格**
- 右侧是 **四联图**：
  - 主图 K 线 + 自定义趋势线
  - 成交额图
  - 砖型差值图
  - KDJ 图
- 关键代码主要在 `app/main.py`、`app/widgets.py`、`app/data_loader.py`
- `main.py` 负责：列表加载、搜索筛选、恢复上次选股、状态栏更新
- `data_loader.py` 负责：股票列表 / 日线 CSV 读取与标准化
- `widgets.py` 是核心：多子图生命周期、K 线绘制、自定义指标、hover 联动、缩放与范围控制
- 搜索支持：代码、名称、拼音首字母、ts_code、行业、地区
- `volume` 实际语义为 **成交额（万元）**，展示时换算为 **亿**
- 当前最重要的稳定性原则：
  - **切换股票时不要重建 plot**
  - 注意 `_loading_plot` / `_updating_range` 防重入
  - hover 联动是整套图表交互核心
