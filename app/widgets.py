from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from .chart_indicators import (
    ZX_MULTI_PERIODS,
    calc_brick_threshold_price,
    compute_brick_indicator,
    compute_kdj_indicator,
    compute_macd_indicator,
    compute_needle20_indicator,
    compute_zx_long_short,
    compute_zx_short_trend,
)
from .chart_interaction import window_width_for_days
from .chart_layout import (
    DEFAULT_SUB_CHARTS,
    FIXED_Y_AXIS_WIDTH,
    SubChartType,
    create_brick_items,
    create_chart_layout,
    create_kdj_items,
    create_macd_items,
    create_needle20_items,
    create_plot_bundle,
    create_price_items,
    create_volume_items,
)
from .chart_overlays import (
    build_brick_delta_label_html,
    build_indicator_label_html,
    build_info_box_html,
    build_kdj_label_html,
    build_macd_label_html,
    build_needle20_label_html,
    build_y_value_html,
)
from .chart_primitives import CandlestickItem
from .chart_ranges import clamp_xrange, padded_min_max, visible_index_range

# 进度对话框已迁移到 app/progress_dialogs.py（保留 re-export 以兼容旧引用）
from .progress_dialogs import ScreeningProgressDialog, UpdateProgressDialog  # noqa: E402,F401

from .chart_widget_hover import HoverMixin
from .chart_widget_panels import PanelsMixin
from .chart_widget_ranges import RangesMixin
from .chart_widget_subcharts import SubChartsMixin


class StockChartWidget(
    PanelsMixin,
    RangesMixin,
    SubChartsMixin,
    HoverMixin,
    QtWidgets.QWidget,
):
    """Daily candlestick + custom indicator lines + amount bars + crosshair."""

    DEFAULT_MIN_VISIBLE_DAYS = 30
    DEFAULT_MAX_VISIBLE_DAYS = 150

    onHover = QtCore.Signal(dict)
    visibleDateRangeChanged = QtCore.Signal(str, str)
    subChartSelectionChanged = QtCore.Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)

        pg.setConfigOptions(antialias=False)

        self._df = None
        self._dates = []
        self._x_min = 0
        self._x_max = 0
        self._loading_plot = False
        self._updating_range = False
        self._short_trend_values = np.array([])
        self._long_short_values = np.array([])
        self._brick_values = np.array([])
        self._brick_green_threshold_items: list[pg.GraphicsObject] = []
        self._kdj_k_values = np.array([])
        self._kdj_d_values = np.array([])
        self._kdj_j_values = np.array([])
        self._needle20_short_values = np.array([])
        self._needle20_mid_values = np.array([])
        self._needle20_long_values = np.array([])
        self._macd_diff_values = np.array([])
        self._macd_dea_values = np.array([])
        self._macd_macd_values = np.array([])
        self._didi_buy_values = np.array([], dtype=bool)
        self._didi_sell_values = np.array([], dtype=bool)
        self._last_hover_index: int | None = None
        self._last_hover_y: float | None = None
        self._last_visible_range_indices: tuple[int, int] | None = None
        self._low_values = np.array([])
        self._high_values = np.array([])
        self._amount_yi_values = np.array([])
        self._last_y_text: str | None = None
        self._item_half_width = CandlestickItem.BODY_WIDTH / 2
        self._right_view_padding = 1.5
        self._min_visible_days = self.DEFAULT_MIN_VISIBLE_DAYS
        self._max_visible_days = self.DEFAULT_MAX_VISIBLE_DAYS

        bundle = create_plot_bundle(self)
        self.priceAxis = bundle.price_axis
        self.volAxis = bundle.vol_axis
        self.brickAxis = bundle.brick_axis
        self.kdjAxis = bundle.kdj_axis
        self.needle20Axis = bundle.needle20_axis
        self.macdAxis = bundle.macd_axis
        self.priceViewBox = bundle.price_viewbox
        self.volViewBox = bundle.vol_viewbox
        self.brickViewBox = bundle.brick_viewbox
        self.kdjViewBox = bundle.kdj_viewbox
        self.needle20ViewBox = bundle.needle20_viewbox
        self.macdViewBox = bundle.macd_viewbox
        self.pricePlot = bundle.price_plot
        self.volPlot = bundle.vol_plot
        self.brickPlot = bundle.brick_plot
        self.kdjPlot = bundle.kdj_plot
        self.needle20Plot = bundle.needle20_plot
        self.macdPlot = bundle.macd_plot

        self._visible_sub_charts: list[SubChartType] = list(DEFAULT_SUB_CHARTS)
        self._type_to_plot: dict[SubChartType, pg.PlotWidget] = {
            SubChartType.VOLUME: self.volPlot,
            SubChartType.BRICK: self.brickPlot,
            SubChartType.KDJ: self.kdjPlot,
            SubChartType.NEEDLE20: self.needle20Plot,
            SubChartType.MACD: self.macdPlot,
        }
        self._type_to_vline: dict[SubChartType, pg.InfiniteLine] = {}

        self.chartContainer, self.chartLayout, layout, date_bar_items, self._separators = create_chart_layout(
            self,
            self.pricePlot,
            self.volPlot,
            self.brickPlot,
            self.kdjPlot,
            self.needle20Plot,
            self.macdPlot,
        )
        self.dateBar = date_bar_items.date_bar
        self.leftDateLabel = date_bar_items.left_date_label
        self.rightDateLabel = date_bar_items.right_date_label
        self.crosshairDateLabel = date_bar_items.crosshair_date_label

        price_items = create_price_items(self.pricePlot)
        self.candleItem = price_items.candle_item
        self.didiMarker = price_items.didi_marker
        self.zx_short_trend = price_items.zx_short_trend
        self.zx_long_short = price_items.zx_long_short
        self.vLine = price_items.v_line
        self.hLine = price_items.h_line
        self.infoText = price_items.info_text
        self.yValueText = price_items.y_value_text
        self.indicatorLabel = price_items.indicator_label
        self.stockInfoLabel = price_items.stock_info_label
        self._price_guide_lines = price_items.price_guide_lines
        self._price_guide_labels = price_items.price_guide_labels

        volume_items = create_volume_items(self.volPlot)
        self.volVLine = volume_items.vol_v_line

        brick_items = create_brick_items(self.brickPlot)
        self.brickDeltaItem = brick_items.brick_delta_item
        self.brickZeroLine = brick_items.brick_zero_line
        self.brickVLine = brick_items.brick_v_line
        self.brickDeltaLabel = brick_items.brick_delta_label

        kdj_items = create_kdj_items(self.kdjPlot)
        self.kdjKCurve = kdj_items.kdj_k_curve
        self.kdjDCurve = kdj_items.kdj_d_curve
        self.kdjJCurve = kdj_items.kdj_j_curve
        self.kdjLowLine = kdj_items.kdj_low_line
        self.kdjMidLine = kdj_items.kdj_mid_line
        self.kdjHighLine = kdj_items.kdj_high_line
        self.kdjVLine = kdj_items.kdj_v_line
        self.kdjLabel = kdj_items.kdj_label

        needle20_items = create_needle20_items(self.needle20Plot)
        self.needle20ShortCurve = needle20_items.short_curve
        self.needle20MidCurve = needle20_items.mid_curve
        self.needle20LongCurve = needle20_items.long_curve
        self.needle20LowLine = needle20_items.low_line
        self.needle20HighLine = needle20_items.high_line
        self.needle20VLine = needle20_items.v_line
        self.needle20Label = needle20_items.label

        macd_items = create_macd_items(self.macdPlot)
        self.macdDiffCurve = macd_items.diff_curve
        self.macdDeaCurve = macd_items.dea_curve
        self.macdZeroLine = macd_items.zero_line
        self.macdVLine = macd_items.v_line
        self.macdLabel = macd_items.label

        self._type_to_vline = {
            SubChartType.VOLUME: self.volVLine,
            SubChartType.BRICK: self.brickVLine,
            SubChartType.KDJ: self.kdjVLine,
            SubChartType.NEEDLE20: self.needle20VLine,
            SubChartType.MACD: self.macdVLine,
        }
        self._type_to_separator: dict[SubChartType, QtWidgets.QWidget] = {
            SubChartType.VOLUME: self._separators.vol_separator,
            SubChartType.BRICK: self._separators.brick_separator,
            SubChartType.KDJ: self._separators.kdj_separator,
            SubChartType.NEEDLE20: self._separators.needle20_separator,
            SubChartType.MACD: self._separators.macd_separator,
        }

        for chart_type in SubChartType:
            if chart_type not in self._visible_sub_charts:
                self._type_to_plot[chart_type].hide()
                self._type_to_separator[chart_type].hide()

        self._proxy = pg.SignalProxy(
            self.pricePlot.scene().sigMouseMoved,
            rateLimit=30,
            slot=lambda evt: self._on_mouse_moved(self.pricePlot, evt),
        )
        self._volProxy = pg.SignalProxy(
            self.volPlot.scene().sigMouseMoved,
            rateLimit=30,
            slot=lambda evt: self._on_mouse_moved(self.volPlot, evt),
        )
        self._brickProxy = pg.SignalProxy(
            self.brickPlot.scene().sigMouseMoved,
            rateLimit=30,
            slot=lambda evt: self._on_mouse_moved(self.brickPlot, evt),
        )
        self._kdjProxy = pg.SignalProxy(
            self.kdjPlot.scene().sigMouseMoved,
            rateLimit=30,
            slot=lambda evt: self._on_mouse_moved(self.kdjPlot, evt),
        )
        self._needle20Proxy = pg.SignalProxy(
            self.needle20Plot.scene().sigMouseMoved,
            rateLimit=30,
            slot=lambda evt: self._on_mouse_moved(self.needle20Plot, evt),
        )
        self._macdProxy = pg.SignalProxy(
            self.macdPlot.scene().sigMouseMoved,
            rateLimit=30,
            slot=lambda evt: self._on_mouse_moved(self.macdPlot, evt),
        )

        for plot in (self.pricePlot, self.volPlot, self.brickPlot, self.kdjPlot, self.needle20Plot, self.macdPlot):
            plot.getViewBox().sigRangeChanged.connect(self._clamp_xrange)

        # 用于在 widget 首次真正显示后修正 Y 轴范围。
        # 弹窗等场景里 set_daily 会在 widget 还没有有效 viewport 尺寸时被调用，
        # 此时 pyqtgraph 内部的 setYRange 写入的 targetRange 会在 widget 首次
        # show/resize 时被 ViewBox 自身的 autoRange 行为覆盖，导致主图上方被遮挡。
        self._initial_yrange_fixed = False

    def showEvent(self, event):
        super().showEvent(event)
        # widget 首次显示后，pyqtgraph 才拿到真实 viewport 尺寸。
        # 延迟到下一个事件循环再修正一次 Y 轴范围，确保覆盖 ViewBox resize
        # 时可能的 autoRange 行为。
        if not self._initial_yrange_fixed and self._df is not None and len(self._df) > 0:
            self._initial_yrange_fixed = True
            QtCore.QTimer.singleShot(0, self._refresh_visible_yrange)

    def set_stock_info(self, symbol: str, name: str = ""):
        """Display stock symbol and name in the top-left corner of the price chart."""
        if name:
            text = f"{name}  {symbol}"
        else:
            text = symbol
        self.stockInfoLabel.setText(text)
        self.stockInfoLabel.adjustSize()
        self.stockInfoLabel.show()

    def set_daily(self, df):
        """df columns: date, open, high, low, close, volume(万元成交额)"""
        self._loading_plot = True
        try:
            self._df = df.reset_index(drop=True)
            self._dates = [d.strftime("%Y-%m-%d") for d in self._df["date"]]
            self._last_visible_range_indices = None
            self._last_hover_y = None

            self._set_date_axis(self._dates)

            self._x_min = -self._item_half_width
            self._x_max = max(0, len(df) - 1) + self._item_half_width + self._right_view_padding
            self._apply_xrange_limits()

            x, o, h, l, c, amount_yi, is_up = self._prepare_daily_arrays(df)
            self._update_price_panel(x, o, h, l, c, is_up)

            if SubChartType.VOLUME in self._visible_sub_charts:
                self._update_volume_panel(x, is_up, amount_yi)

            if SubChartType.BRICK in self._visible_sub_charts:
                brick_values = self._update_brick_panel(x, h, l, c)
            else:
                brick_result = compute_brick_indicator(h, l, c)
                brick_values = brick_result["brick"]
                self._brick_values = brick_values
            self._update_brick_green_thresholds(h, l, c, brick_values)

            if SubChartType.KDJ in self._visible_sub_charts:
                self._update_kdj_panel(x, h, l, c)
            else:
                kdj_result = compute_kdj_indicator(h, l, c)
                self._kdj_k_values = kdj_result["k"]
                self._kdj_d_values = kdj_result["d"]
                self._kdj_j_values = kdj_result["j"]

            if SubChartType.NEEDLE20 in self._visible_sub_charts:
                self._update_needle20_panel(x, h, l, c)
            else:
                n20_result = compute_needle20_indicator(h, l, c)
                self._needle20_short_values = n20_result["short"]
                self._needle20_mid_values = n20_result["mid"]
                self._needle20_long_values = n20_result["long"]

            if SubChartType.MACD in self._visible_sub_charts:
                self._update_macd_panel(x, c)
            else:
                macd_result = compute_macd_indicator(c)
                self._macd_diff_values = macd_result["diff"]
                self._macd_dea_values = macd_result["dea"]
                self._macd_macd_values = macd_result["macd"]

            self._reset_initial_view(df, brick_values)
        finally:
            self._loading_plot = False
