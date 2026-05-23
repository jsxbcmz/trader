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


class RangesMixin:
    def _refresh_visible_yrange(self):
        """强制基于当前 X 视图重算 Y 轴范围，绕过缓存短路。"""
        if self._df is None or len(self._df) == 0:
            return
        (x0, x1), _ = self.pricePlot.getViewBox().viewRange()
        self._last_visible_range_indices = None
        self._update_visible_yrange(x0, x1)

    def _window_width_for_days(self, days: int, include_right_padding: bool):
        return window_width_for_days(days, self._item_half_width, self._right_view_padding, include_right_padding)

    def _apply_xrange_limits(self):
        if self._df is None or len(self._df) == 0:
            return

        x_min_allowed = float(self._x_min)
        x_max_allowed = float(self._x_max)
        full_width = x_max_allowed - x_min_allowed
        if full_width <= 0:
            return

        min_width = min(self._window_width_for_days(self._min_visible_days, include_right_padding=False), full_width)
        max_width = min(self._window_width_for_days(self._max_visible_days, include_right_padding=True), full_width)
        if max_width < min_width:
            max_width = min_width

        plots = [self.pricePlot] + [self._type_to_plot[t] for t in self._visible_sub_charts]
        for plot in plots:
            plot.getViewBox().setLimits(
                xMin=x_min_allowed,
                xMax=x_max_allowed,
                minXRange=min_width,
                maxXRange=max_width,
            )

    def set_visible_day_limits(self, min_days: int, max_days: int):
        min_value = max(int(min_days), 1)
        max_value = max(int(max_days), min_value + 1)
        self._min_visible_days = min_value
        self._max_visible_days = max_value
        self._apply_xrange_limits()

        if self._df is None or len(self._df) == 0:
            return

        viewbox = self.pricePlot.getViewBox()
        current_range = viewbox.viewRange()
        self._clamp_xrange(viewbox, current_range)

    def _clamp_xrange(self, viewbox, range_):
        """Limit horizontal panning/zooming within [x_min, x_max]."""
        if self._df is None or len(self._df) == 0 or self._loading_plot or self._updating_range:
            return

        self._updating_range = True
        try:
            x_min_allowed = float(self._x_min)
            x_max_allowed = float(self._x_max)
            full_width = x_max_allowed - x_min_allowed
            if full_width <= 0:
                return

            (x0, x1), _ = range_
            min_width = min(self._window_width_for_days(self._min_visible_days, include_right_padding=False), full_width)
            max_width = min(self._window_width_for_days(self._max_visible_days, include_right_padding=True), full_width)
            if max_width < min_width:
                max_width = min_width

            clamped = clamp_xrange(x0, x1, x_min_allowed, x_max_allowed, min_width, max_width)
            if clamped is None:
                return

            new_x0, new_x1 = clamped
            if abs(new_x0 - x0) > 1e-6 or abs(new_x1 - x1) > 1e-6:
                self.pricePlot.setXRange(new_x0, new_x1, padding=0)
                return

            self._update_visible_yrange(new_x0, new_x1)
        finally:
            self._updating_range = False

    def _update_visible_yrange(self, x0: float, x1: float):
        """Fit y-range to currently visible candles with a small vertical padding."""
        if self._df is None or len(self._df) == 0:
            return

        visible_indices = visible_index_range(x0, x1, self._item_half_width, len(self._df))
        if visible_indices is None:
            return
        if self._last_visible_range_indices == visible_indices:
            return
        self._last_visible_range_indices = visible_indices

        left, right = visible_indices
        if self._dates:
            d0 = self._dates[max(0, left)]
            d1 = self._dates[min(right, len(self._dates) - 1)]
            self.visibleDateRangeChanged.emit(d0, d1)
        visible_low = self._low_values[left:right + 1]
        visible_high = self._high_values[left:right + 1]
        y_range = padded_min_max(visible_low, visible_high)
        if y_range is None:
            return

        ymin, ymax = y_range
        self.pricePlot.setYRange(ymin, ymax, padding=0)
        self._update_price_guide_lines(ymin, ymax)

        if SubChartType.VOLUME in self._visible_sub_charts and len(self._amount_yi_values) > 0:
            visible_amount = self._amount_yi_values[left:right + 1]
            finite_amount = visible_amount[np.isfinite(visible_amount)]
            if len(finite_amount) > 0:
                vol_max = float(np.max(finite_amount))
                vol_pad = vol_max * 0.02
                self.volPlot.setYRange(0, vol_max + vol_pad, padding=0)

        if SubChartType.BRICK in self._visible_sub_charts and len(self._brick_values) > 0:
            visible_brick = self._brick_values[left:right + 1]
            finite_brick = visible_brick[np.isfinite(visible_brick)]
            if len(finite_brick) > 0:
                prev_brick = np.full_like(visible_brick, np.nan)
                if left > 0:
                    prev_brick[0] = self._brick_values[left - 1]
                if len(visible_brick) > 1:
                    prev_brick[1:] = visible_brick[:-1]
                starts = np.where(np.isfinite(prev_brick), prev_brick, 0.0)
                brick_lows = np.minimum(starts, visible_brick)
                brick_highs = np.maximum(starts, visible_brick)
                finite_lows = brick_lows[np.isfinite(brick_lows)]
                finite_highs = brick_highs[np.isfinite(brick_highs)]
                if len(finite_lows) > 0 and len(finite_highs) > 0:
                    brick_min = float(np.min(finite_lows))
                    brick_max = float(np.max(finite_highs))
                    brick_pad = max((brick_max - brick_min) * 0.08, 0.1)
                    self.brickPlot.setYRange(brick_min - brick_pad, brick_max + brick_pad, padding=0)

        if SubChartType.NEEDLE20 in self._visible_sub_charts:
            vis_short = self._needle20_short_values[left:right + 1] if len(self._needle20_short_values) > 0 else np.array([])
            vis_mid = self._needle20_mid_values[left:right + 1] if len(self._needle20_mid_values) > 0 else np.array([])
            vis_long = self._needle20_long_values[left:right + 1] if len(self._needle20_long_values) > 0 else np.array([])
            all_vis = np.concatenate([
                vis_short[np.isfinite(vis_short)] if len(vis_short) > 0 else np.array([]),
                vis_mid[np.isfinite(vis_mid)] if len(vis_mid) > 0 else np.array([]),
                vis_long[np.isfinite(vis_long)] if len(vis_long) > 0 else np.array([]),
            ])
            if len(all_vis) > 0:
                n20_min = min(float(np.min(all_vis)), 0.0)
                n20_max = max(float(np.max(all_vis)), 100.0)
                n20_pad = max((n20_max - n20_min) * 0.06, 1.0)
                self.needle20Plot.setYRange(n20_min - n20_pad, n20_max + n20_pad, padding=0)

        if SubChartType.MACD in self._visible_sub_charts:
            vis_diff = self._macd_diff_values[left:right + 1] if len(self._macd_diff_values) > 0 else np.array([])
            vis_dea = self._macd_dea_values[left:right + 1] if len(self._macd_dea_values) > 0 else np.array([])
            vis_macd = self._macd_macd_values[left:right + 1] if len(self._macd_macd_values) > 0 else np.array([])
            all_vis = np.concatenate([
                vis_diff[np.isfinite(vis_diff)] if len(vis_diff) > 0 else np.array([]),
                vis_dea[np.isfinite(vis_dea)] if len(vis_dea) > 0 else np.array([]),
                vis_macd[np.isfinite(vis_macd)] if len(vis_macd) > 0 else np.array([]),
            ])
            if len(all_vis) > 0:
                macd_min = float(np.min(all_vis))
                macd_max = float(np.max(all_vis))
                macd_pad = max((macd_max - macd_min) * 0.06, 0.1)
                self.macdPlot.setYRange(macd_min - macd_pad, macd_max + macd_pad, padding=0)

        if len(self._dates) > 0:
            left_date = self._dates[left] if left < len(self._dates) else ""
            right_date = self._dates[min(right, len(self._dates) - 1)]
            self.leftDateLabel.setText(left_date)
            self.rightDateLabel.setText(right_date)

    def _update_price_guide_lines(self, ymin: float, ymax: float):
        """Place evenly-spaced horizontal dashed guide lines across the visible price range."""
        num_guides = len(self._price_guide_lines)
        price_range = ymax - ymin
        if price_range <= 0 or not np.isfinite(price_range):
            for line in self._price_guide_lines:
                line.hide()
            for label in self._price_guide_labels:
                label.hide()
            return

        step = price_range / (num_guides + 1)
        view_range = self.pricePlot.getViewBox().viewRange()
        x_left = view_range[0][0]

        for i in range(num_guides):
            price = ymin + step * (i + 1)
            self._price_guide_lines[i].setPos(price)
            self._price_guide_lines[i].show()

            price_text = f"{price:.2f}"
            self._price_guide_labels[i].setText(price_text)
            self._price_guide_labels[i].setPos(x_left, price)
            self._price_guide_labels[i].show()

    def _reset_initial_view(self, df, brick_values):
        initial_data_idx = max(0, len(df) - 1)
        initial_x0 = max(self._x_min, len(df) - 100 - self._item_half_width)
        initial_x1 = min(self._x_max, initial_data_idx + self._item_half_width + self._right_view_padding)
        initial_x0 = max(self._x_min, min(initial_x0, initial_x1 - (99 + self._item_half_width * 2)))
        self.pricePlot.setXRange(initial_x0, initial_x1, padding=0)
        self._update_visible_yrange(initial_x0, initial_x1)
        self._set_crosshair_x(float(initial_data_idx))
        self._last_hover_index = int(initial_data_idx)
        self._last_hover_y = None
        self._last_y_text = None
        self._update_indicator_label_values(initial_data_idx)
        self._update_brick_delta_label(initial_data_idx, brick_values)
        self._update_kdj_label_values(initial_data_idx)
        self._update_needle20_label_values(initial_data_idx)
        self._update_macd_label_values(initial_data_idx)
        self.infoText.hide()
        self.yValueText.hide()

    def _set_axes_dates(self, axis):
        axis._dates = self._dates

    def _reset_axis_picture(self, plot):
        bottom_axis = plot.getAxis("bottom")
        bottom_axis.setTicks(None)
        bottom_axis.picture = None
        bottom_axis.update()

    def _set_date_axis(self, dates: list[str]):
        """Update bottom axis date strings without recreating plots."""
        self._dates = dates
        type_to_axis = {
            SubChartType.VOLUME: self.volAxis,
            SubChartType.BRICK: self.brickAxis,
            SubChartType.KDJ: self.kdjAxis,
            SubChartType.NEEDLE20: self.needle20Axis,
            SubChartType.MACD: self.macdAxis,
        }
        axes = [self.priceAxis] + [type_to_axis[t] for t in self._visible_sub_charts]
        for axis in axes:
            self._set_axes_dates(axis)
        plots = [self.pricePlot] + [self._type_to_plot[t] for t in self._visible_sub_charts]
        for plot in plots:
            self._reset_axis_picture(plot)
