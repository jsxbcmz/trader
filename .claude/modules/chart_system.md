# 四联图系统

四联图是本项目核心可视化组件，由 7 个文件协作构成。四个面板 X 轴联动。

## 文件总览

| 文件 | 行数 | 职责 |
|------|------|------|
| `app/widgets.py` | ~933 | StockChartWidget 主组件 + 进度弹窗 |
| `app/chart_layout.py` | ~357 | PlotBundle/Items dataclass + 工厂函数 |
| `app/chart_primitives.py` | ~161 | CandlestickItem, BrickDeltaItem, DateAxisItem |
| `app/chart_indicators.py` | ~245 | 指标计算（EMA, MA, SMA, KDJ, Brick, 临界价格） |
| `app/chart_overlays.py` | ~92 | HTML 浮窗/标签构建 |
| `app/chart_interaction.py` | ~45 | StockChartViewBox 鼠标交互 |
| `app/chart_ranges.py` | ~60 | 范围 clamp 逻辑 |

## 模块依赖关系

```
widgets.py (StockChartWidget)
    ├── chart_layout.py        (工厂函数：创建所有 pyqtgraph 对象)
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

### 关键公开方法

| 方法 | 说明 |
|------|------|
| `set_daily(df)` | 核心数据加载入口，驱动全部四个子图更新 |
| `set_stock_info(symbol, name)` | 价格图左上角显示股票信息 |
| `set_visible_day_limits(min_days, max_days)` | 设置水平缩放范围 |

### 关键内部方法

| 方法 | 说明 |
|------|------|
| `_prepare_daily_arrays(df)` | DataFrame → numpy 数组 |
| `_update_price_panel(x, o, h, l, c)` | 更新蜡烛图+指标线 |
| `_update_volume_panel(x, o, c, amount_yi)` | 重绘成交额柱状图 |
| `_update_brick_panel(x, h, l, c)` | 计算+绘制砖型差值 |
| `_update_kdj_panel(x, h, l, c)` | 计算+绘制 KDJ |
| `_clamp_xrange(viewbox, range_)` | sigRangeChanged 回调，限制平移/缩放 |
| `_update_visible_yrange(x0, x1)` | Y 轴自适应 + 日期标签更新 |
| `_on_mouse_moved(source_plot, evt)` | 四图鼠标移动处理（30fps 限速） |
| `_update_hover_for_index(idx, y, ...)` | 统一更新十字线/浮窗/标签/信号 |
| `_set_crosshair_x(x)` | 同步四图竖直十字线 |

### 关键状态变量

| 变量 | 说明 |
|------|------|
| `_df` | 当前加载的 DataFrame |
| `_dates` | 日期字符串列表 |
| `_x_min / _x_max` | 横轴允许范围 |
| `_loading_plot / _updating_range` | 重入保护标志 |
| `_short_trend_values / _long_short_values` | 趋势线/多空线缓存 |
| `_brick_values / _kdj_k/d/j_values` | 砖型/KDJ 指标缓存 |

### 数据流

```
DataFrame → set_daily() → _prepare_daily_arrays()
  → _update_price_panel()   [蜡烛图 + EMA + MA]
  → _update_volume_panel()   [红绿柱]
  → _update_brick_panel()    [砖型差值]
  → _update_kdj_panel()      [K/D/J 三线]
  → _reset_initial_view()    [滚动到最近100根]

鼠标移动 → _on_mouse_moved() → _update_hover_for_index()
  → 十字线同步 + 浮窗HTML + 指标标签 + onHover信号
```

---

## chart_layout.py — 布局工厂

### Dataclass（均 `slots=True`）

| 类名 | 说明 |
|------|------|
| `PlotBundle` | 4×Axis + 4×ViewBox + 4×PlotWidget |
| `PriceItems` | K线面板所有图形项（蜡烛、曲线、十字线、浮窗、参考线等） |
| `VolumeItems` | 成交额面板（仅竖线） |
| `BrickItems` | 砖型差值面板图形项 |
| `KdjItems` | KDJ 面板图形项（三曲线+三参考线） |
| `DateBarItems` | 底部日期标注栏 |

### 工厂函数

| 函数 | 说明 |
|------|------|
| `create_plot_bundle(owner)` | 创建四个 DateAxisItem + StockChartViewBox + PlotWidget，链接 X 轴 |
| `create_chart_layout(owner, ...)` | 组装 QVBoxLayout（比例 3:1:1:1） |
| `create_price_items(price_plot)` | 蜡烛图+指标线+十字线+浮窗+参考线 |
| `create_volume_items(vol_plot)` | 竖线 |
| `create_brick_items(brick_plot)` | BrickDeltaItem+零线+竖线+标签 |
| `create_kdj_items(kdj_plot)` | K/D/J 三线+20/50/80 参考线 |

**常量：** `FIXED_Y_AXIS_WIDTH = 50`

---

## chart_primitives.py — 自定义图元

### DateAxisItem(pg.AxisItem)
将整数索引映射为 `YYYY-MM-DD` 字符串的 X 轴。`_dates: list[str]` 可外部直接赋值替换。

### CandlestickItem(pg.GraphicsObject)
蜡烛图绘图项（QPainter 手绘），`BODY_WIDTH = 0.6`。
- `setData(data: ndarray)` — shape `(N, 5)` → `[x, open, high, low, close]`
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
| `calc_brick_threshold_price(h, l, c, idx, target)` | 计算砖型差值恰好为零的临界收盘价 |

---

## chart_overlays.py — HTML 构建

纯函数模块，无类。

| 函数 | 说明 |
|------|------|
| `build_indicator_label_html(short_trend, long_short)` | 趋势线+多空线标签 |
| `build_brick_delta_label_html(diff)` | 砖型差值标签（红/绿/灰） |
| `build_kdj_label_html(k, d, j)` | KDJ 标签（白/金/品红） |
| `build_y_value_html(text)` | Y 轴价格标签 |
| `build_info_box_html(ds, close, pct, amount)` | 完整悬浮信息框 |

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
