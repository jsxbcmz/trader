# 四联图系统

核心可视化组件，由 7 个文件协作构成。价格面板 + 可配置子图面板（成交额/砖型差值/KDJ/单针下20/MACD），X 轴联动。

## 文件总览

| 文件 | 行数 | 职责 |
|------|------|------|
| `app/widgets.py` | ~1340 | StockChartWidget 主组件 + 进度弹窗 |
| `app/chart_layout.py` | ~574 | SubChartType 枚举 + PlotBundle/Items dataclass + 工厂函数 + SubChartSelector |
| `app/chart_primitives.py` | ~169 | CandlestickItem, BrickDeltaItem, DateAxisItem |
| `app/chart_indicators.py` | ~280 | 指标计算（EMA, MA, SMA, KDJ, Brick, MACD, Needle20, 临界价格） |
| `app/chart_overlays.py` | ~118 | HTML 浮窗/标签构建 |
| `app/chart_interaction.py` | ~45 | StockChartViewBox 鼠标交互 |
| `app/chart_ranges.py` | ~60 | 范围 clamp 逻辑 |

## 模块依赖关系

```
widgets.py (StockChartWidget)
    ├── chart_layout.py        (SubChartType 枚举 + 工厂函数：创建所有 pyqtgraph 对象)
    │       ├── chart_primitives.py    (DateAxisItem, CandlestickItem, BrickDeltaItem)
    │       └── chart_interaction.py   (StockChartViewBox)
    ├── chart_indicators.py    (compute_* 函数，运行时调用)
    ├── chart_overlays.py      (build_*_html 函数，悬停时调用)
    ├── chart_ranges.py        (clamp_xrange, visible_index_range, padded_min_max)
    └── chart_interaction.py   (window_width_for_days)
```

---

## widgets.py — StockChartWidget

### 类结构

**UpdateProgressDialog(QDialog)** — 批量更新进度弹窗，含进度条、日志、取消/关闭按钮
**ScreeningProgressDialog(QDialog)** — 选股进度弹窗，含停止/关闭按钮

**StockChartWidget(QWidget)** — 核心图表组件

**常量：** `DEFAULT_MIN_VISIBLE_DAYS = 30`, `DEFAULT_MAX_VISIBLE_DAYS = 150`
**信号：** `onHover = Signal(dict)` — 鼠标悬停时发射 OHLCV 数据字典

### 子图可见性管理

通过 `_visible_sub_charts: list[SubChartType]` 控制哪些子图面板可见，默认为 `[VOLUME, BRICK, KDJ]`。

| 方法 | 说明 |
|------|------|
| `set_visible_sub_charts(types)` | 动态切换可见子图，隐藏/显示对应面板和分隔线 |
| `_render_newly_visible(newly_visible)` | 对新显示的子图重新渲染数据 |
| `_relink_x_axes()` | 重新链接所有可见子图的 X 轴 |

### 关键公开方法

| 方法 | 说明 |
|------|------|
| `set_daily(df)` | 核心数据加载入口，驱动全部面板更新 |
| `set_stock_info(symbol, name)` | 价格图左上角显示股票信息 |
| `set_visible_day_limits(min_days, max_days)` | 设置水平缩放范围 |
| `set_visible_sub_charts(types)` | 设置可见子图列表 |

### 关键内部方法

| 方法 | 说明 |
|------|------|
| `_prepare_daily_arrays(df)` | DataFrame → numpy 数组 |
| `_update_price_panel(x, o, h, l, c, is_up)` | 更新蜡烛图+指标线 |
| `_update_volume_panel(x, is_up, amount_yi)` | 重绘成交额柱状图 |
| `_update_brick_panel(x, h, l, c)` | 计算+绘制砖型差值 |
| `_update_kdj_panel(x, h, l, c)` | 计算+绘制 KDJ |
| `_update_needle20_panel(x, h, l, c)` | 计算+绘制单针下20 |
| `_update_macd_panel(x, c)` | 计算+绘制 MACD |
| `_update_brick_green_thresholds(h, l, c, brick_values)` | 砖型差值绿砖阈值参考线 |
| `_update_price_guide_lines(ymin, ymax)` | 价格面板参考线 |
| `_clamp_xrange(viewbox, range_)` | sigRangeChanged 回调，限制平移/缩放 |
| `_update_visible_yrange(x0, x1)` | Y 轴自适应 + 日期标签更新 |
| `_on_mouse_moved(source_plot, evt)` | 多图鼠标移动处理（30fps 限速） |
| `_update_hover_for_index(idx, y, show_price_overlay)` | 统一更新十字线/浮窗/标签/信号 |
| `_set_crosshair_x(x)` | 同步所有可见面板竖直十字线 |
| `_update_info_box(idx, x, y)` | 更新悬浮信息框（含 OHLCV + 换手率） |

### 关键状态变量

| 变量 | 说明 |
|------|------|
| `_df` | 当前加载的 DataFrame |
| `_dates` | 日期字符串列表 |
| `_x_min / _x_max` | 横轴允许范围 |
| `_loading_plot / _updating_range` | 重入保护标志 |
| `_visible_sub_charts` | 当前可见子图列表 |
| `_short_trend_values / _long_short_values` | 趋势线/多空线缓存 |
| `_brick_values / _kdj_k/d/j_values` | 砖型/KDJ 指标缓存 |
| `_needle20_short/mid/long_values` | 单针下20 指标缓存 |
| `_macd_diff/dea/macd_values` | MACD 指标缓存 |

### 数据流

```
DataFrame → set_daily() → _prepare_daily_arrays()
  → _update_price_panel()      [蜡烛图 + EMA + MA]
  → _update_volume_panel()     [红绿柱]（如可见）
  → _update_brick_panel()      [砖型差值]（如可见）
  → _update_kdj_panel()        [K/D/J 三线]（如可见）
  → _update_needle20_panel()   [单针下20]（如可见）
  → _update_macd_panel()       [MACD]（如可见）
  → _reset_initial_view()      [滚动到最近100根]

鼠标移动 → _on_mouse_moved() → _update_hover_for_index()
  → 十字线同步 + 浮窗HTML + 指标标签 + onHover信号
```

---

## chart_layout.py — 布局工厂

### SubChartType(str, Enum)

可选子图类型枚举：

| 值 | 显示名 | 默认序号 |
|----|--------|---------|
| `VOLUME` | 成交额 | 0 |
| `BRICK` | 砖型差值 | 1 |
| `KDJ` | KDJ | 2 |
| `NEEDLE20` | 单针下20 | 3 |
| `MACD` | MACD | 4 |

**常量：** `DEFAULT_SUB_CHARTS = [VOLUME, BRICK, KDJ]`

### Dataclass

| 类名 | 说明 |
|------|------|
| `PlotBundle` | 价格+5种子图的 Axis/ViewBox/PlotWidget |
| `PriceItems` | K线面板所有图形项（蜡烛、曲线、十字线、浮窗、参考线等） |
| `VolumeItems` | 成交额面板（竖线） |
| `BrickItems` | 砖型差值面板图形项 |
| `KdjItems` | KDJ 面板图形项（三曲线+三参考线） |
| `Needle20Items` | 单针下20面板图形项（三曲线+参考线） |
| `MacdItems` | MACD 面板图形项（DIFF/DEA曲线+柱状图） |
| `SubChartSeparators` | 子图间分隔线 Widget |
| `DateBarItems` | 底部日期标注栏 |

### 工厂函数

| 函数 | 说明 |
|------|------|
| `create_plot_bundle(owner)` | 创建价格+5种子图的 DateAxisItem + StockChartViewBox + PlotWidget，链接 X 轴 |
| `create_chart_layout(owner, ...)` | 组装 QVBoxLayout（价格面板+动态子图面板） |
| `create_price_items(price_plot)` | 蜡烛图+指标线+十字线+浮窗+参考线 |
| `create_volume_items(vol_plot)` | 竖线 |
| `create_brick_items(brick_plot)` | BrickDeltaItem+零线+竖线+标签 |
| `create_kdj_items(kdj_plot)` | K/D/J 三线+20/50/80 参考线 |
| `create_needle20_items(needle20_plot)` | 短/中/长三线+参考线 |
| `create_macd_items(macd_plot)` | DIFF/DEA 曲线+MACD 柱状图 |

### SubChartSelector(QToolButton)

下拉多选按钮，用户可动态切换可见子图。

**信号：** `selectionChanged = Signal(list)` — 勾选变化时发射

| 方法 | 说明 |
|------|------|
| `get_selected()` | 返回当前勾选的 SubChartType 列表 |
| `set_selected(types)` | 设置勾选状态 |

**常量：** `FIXED_Y_AXIS_WIDTH = 50`

---

## chart_primitives.py — 自定义图元

### DateAxisItem(pg.AxisItem)
将整数索引映射为 `YYYY-MM-DD` 字符串的 X 轴。`_dates: list[str]` 可外部直接赋值替换。

### CandlestickItem(pg.GraphicsObject)
蜡烛图绘图项（QPainter 手绘），`BODY_WIDTH = 0.6`。
- `setData(data: ndarray, is_up: ndarray)` — shape `(N, 5)` → `[x, open, high, low, close]`
- 上涨红 `(220,0,0)`，下跌绿 `(0,170,0)`

### BrickDeltaItem(pg.GraphicsObject)
砖型差值柱状图（QPainter 手绘），`BODY_WIDTH = 0.6`。
- `setData(data: ndarray)` — shape `(N, 3)` → `[x, prev_brick, current_brick]`

---

## chart_indicators.py — 指标计算

支持 Numba JIT 加速（未安装时回退纯 Python）。

**常量：** `ZX_MULTI_PERIODS = (14, 28, 57, 114)`

| 函数 | 说明 |
|------|------|
| `rolling_max/min(values, period)` | pandas rolling 最高/最低值 |
| `tdx_sma(values, n, m)` | 通达信 SMA 公式 |
| `moving_average(values, period)` | 简单 MA（np.convolve） |
| `ema(values, period)` | 标准 EMA |
| `compute_zx_short_trend(close)` | `ema(ema(close, 10), 10)` 双重平滑 |
| `compute_zx_long_short(close, periods)` | 四周期 MA 均值 |
| `compute_brick_indicator(high, low, close)` | 砖型差值完整计算，返回 dict |
| `compute_kdj_indicator(high, low, close)` | 标准 KDJ，返回 `{k, d, j}` |
| `compute_macd_indicator(close)` | MACD，返回 `{diff, dea, macd, cross_up, cross_down}` |
| `compute_needle20_indicator(high, low, close)` | 单针下20，返回 `{short, mid, long}` |
| `calc_brick_threshold_price(h, l, c, idx, target)` | 计算砖型差值恰好为零的临界收盘价 |

---

## chart_overlays.py — HTML 构建

纯函数模块，无类。

| 函数 | 说明 |
|------|------|
| `format_numeric(value)` | 数值格式化（保留2位小数） |
| `build_indicator_label_html(short_trend, long_short)` | 趋势线+多空线标签 |
| `build_brick_delta_label_html(diff)` | 砖型差值标签（红/绿/灰） |
| `build_kdj_label_html(k, d, j)` | KDJ 标签（白/金/品红） |
| `build_macd_label_html(diff, dea, macd)` | MACD 标签（白/金+红绿柱色） |
| `build_needle20_label_html(short, mid, long)` | 单针下20标签（白/金/品红） |
| `build_y_value_html(text)` | Y 轴价格标签 |
| `format_tooltip_value(value, nd)` | 悬浮提示数值格式化 |
| `build_info_box_html(ds, close, pct, amount, turnover_rate, open, high, low)` | 完整悬浮信息框（含 OHLCV + 换手率） |

**颜色规范：** 上涨 `#ff4d4f` / 下跌 `#00b050` / 中性 `#d9d9d9`

---

## chart_interaction.py — 鼠标交互

**`window_width_for_days(days, half_width, padding, include_padding)`** — 可见天数 → 视图坐标宽度

**StockChartViewBox(pg.ViewBox)** — 重写滚轮事件，边界处吞掉事件阻止继续缩放。持有 `_owner` 引用 StockChartWidget。

---

## chart_ranges.py — 范围控制

纯函数模块。

| 函数 | 说明 |
|------|------|
| `clamp_xrange(x0, x1, ...)` | X 轴范围约束（宽度限制 + 边界平移） |
| `visible_index_range(x0, x1, ...)` | 视图坐标 → 数组索引范围 |
| `padded_min_max(low, high)` | 可见区域 Y 轴范围（+4% padding） |
