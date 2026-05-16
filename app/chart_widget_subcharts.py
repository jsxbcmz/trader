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


class SubChartsMixin:
    def set_visible_sub_charts(self, types: list[SubChartType]):
        """设置可见的副图面板，动态调整布局。"""
        if not types:
            return
        types = [t for t in SubChartType if t in types]
        if not types:
            return
        old_visible = set(self._visible_sub_charts)
        self._visible_sub_charts = types

        visible_count = len(types)
        price_stretch = 6 - visible_count

        self.chartLayout.setStretchFactor(self.pricePlot, price_stretch)

        for chart_type in SubChartType:
            plot = self._type_to_plot[chart_type]
            separator = self._type_to_separator[chart_type]
            if chart_type in types:
                separator.show()
                plot.show()
                self.chartLayout.setStretchFactor(plot, 1)
            else:
                separator.hide()
                plot.hide()
                self.chartLayout.setStretchFactor(plot, 0)

        self._relink_x_axes()

        if self._df is not None and len(self._df) > 0:
            newly_visible = set(types) - old_visible
            if newly_visible:
                self._render_newly_visible(newly_visible)
            self._apply_xrange_limits()
            vb = self.pricePlot.getViewBox()
            self._clamp_xrange(vb, vb.viewRange())

    def _apply_sub_chart_selection(self, types: list[SubChartType]):
        self.set_visible_sub_charts(types)
        self.subChartSelectionChanged.emit(list(self._visible_sub_charts))

    def _render_newly_visible(self, newly_visible: set[SubChartType]):
        x, o, h, l, c, amount_yi, is_up = self._prepare_daily_arrays(self._df)
        if SubChartType.VOLUME in newly_visible:
            self._update_volume_panel(x, is_up, amount_yi)
        if SubChartType.BRICK in newly_visible:
            self._update_brick_panel(x, h, l, c)
        if SubChartType.KDJ in newly_visible:
            self._update_kdj_panel(x, h, l, c)
        if SubChartType.NEEDLE20 in newly_visible:
            self._update_needle20_panel(x, h, l, c)
        if SubChartType.MACD in newly_visible:
            self._update_macd_panel(x, c)

    def _relink_x_axes(self):
        for plot in (self.volPlot, self.brickPlot, self.kdjPlot, self.needle20Plot, self.macdPlot):
            plot.setXLink(None)
        for chart_type in self._visible_sub_charts:
            self._type_to_plot[chart_type].setXLink(self.pricePlot)
