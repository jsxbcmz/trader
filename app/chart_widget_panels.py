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


class PanelsMixin:
    def _prepare_daily_arrays(self, df):
        x = np.arange(len(df), dtype=float)
        o = df["open"].to_numpy(float)
        h = df["high"].to_numpy(float)
        l = df["low"].to_numpy(float)
        c = df["close"].to_numpy(float)
        amount_yi = df["volume"].to_numpy(float) / 1e4
        # 涨跌方向：基于 close vs 前一日 close，首日 fallback 到 close vs open
        pre_close = np.empty_like(c)
        pre_close[0] = o[0]
        pre_close[1:] = c[:-1]
        is_up = c >= pre_close
        return x, o, h, l, c, amount_yi, is_up

    def _update_price_panel(self, x, o, h, l, c, is_up):
        self._low_values = l
        self._high_values = h
        self.candleItem.setData(np.column_stack([x, o, h, l, c]), is_up=is_up)

        short_trend = compute_zx_short_trend(c)
        long_short = compute_zx_long_short(c, periods=ZX_MULTI_PERIODS)
        self._short_trend_values = short_trend
        self._long_short_values = long_short
        self.zx_short_trend.setData(x, short_trend)
        self.zx_long_short.setData(x, long_short)

    def _update_brick_green_thresholds(self, h, l, c, brick_values):
        """在主图用横向虚线标注最后一天砖形图转绿的临界价格。

        当最后一天砖形图为红砖（brick > prev_brick）时，计算该天收盘价
        低于多少砖形图就会变绿，并在主图上画一根横向虚线。
        """
        for item in self._brick_green_threshold_items:
            self.pricePlot.removeItem(item)
        self._brick_green_threshold_items.clear()

        if len(brick_values) < 2:
            return

        last_index = len(brick_values) - 1
        current_brick = brick_values[last_index]
        prev_brick_val = brick_values[last_index - 1]

        is_red = current_brick > prev_brick_val
        if not is_red:
            return

        threshold = calc_brick_threshold_price(h, l, c, last_index, prev_brick_val)
        if threshold is None:
            return

        line = pg.InfiniteLine(
            pos=threshold,
            angle=0,
            movable=False,
            pen=pg.mkPen((0, 200, 80, 180), width=1, style=QtCore.Qt.DashLine),
        )
        line.setZValue(500)
        self.pricePlot.addItem(line, ignoreBounds=True)
        self._brick_green_threshold_items.append(line)

        left_label = pg.TextItem(
            text=f"{threshold:.2f}",
            anchor=(1, 0.5),
            color=(0, 200, 80),
        )
        left_label.setFont(pg.QtGui.QFont("sans-serif", 9))
        left_label.setPos(0, threshold)
        left_label.setZValue(500)
        self.pricePlot.addItem(left_label)
        self._brick_green_threshold_items.append(left_label)

    def _update_volume_panel(self, x, is_up, amount_yi):
        self.volPlot.clear()
        self.volPlot.addItem(self.volVLine, ignoreBounds=True)
        self.volVLine.show()
        self._amount_yi_values = amount_yi

        up_mask = is_up
        down_mask = ~up_mask
        if np.any(up_mask):
            self.volPlot.addItem(
                pg.BarGraphItem(
                    x=x[up_mask],
                    height=amount_yi[up_mask],
                    width=0.6,
                    brush=pg.mkBrush(220, 0, 0, 200),
                    pen=None,
                )
            )
        if np.any(down_mask):
            self.volPlot.addItem(
                pg.BarGraphItem(
                    x=x[down_mask],
                    height=amount_yi[down_mask],
                    width=0.6,
                    brush=pg.mkBrush(0, 170, 0, 200),
                    pen=None,
                )
            )

    def _update_brick_panel(self, x, h, l, c):
        self.brickPlot.clear()
        self.brickPlot.addItem(self.brickDeltaItem)
        self.brickPlot.addItem(self.brickZeroLine, ignoreBounds=True)
        self.brickZeroLine.setPos(0)
        self.brickPlot.addItem(self.brickVLine, ignoreBounds=True)
        self.brickVLine.show()

        brick_result = compute_brick_indicator(h, l, c)
        brick_values = brick_result["brick"]
        self._brick_values = brick_values

        prev_brick = np.full_like(brick_values, np.nan)
        if len(brick_values) > 1:
            prev_brick[1:] = brick_values[:-1]

        brick_segment_data = (
            np.column_stack([x, prev_brick, brick_values])
            if len(brick_values) > 0
            else np.empty((0, 3), dtype=float)
        )
        self.brickDeltaItem.setData(brick_segment_data)


        if len(brick_values) > 0:
            segment_starts = np.where(np.isfinite(prev_brick), prev_brick, 0.0)
            segment_lows = np.minimum(segment_starts, brick_values)
            segment_highs = np.maximum(segment_starts, brick_values)
            finite_lows = segment_lows[np.isfinite(segment_lows)]
            finite_highs = segment_highs[np.isfinite(segment_highs)]
        else:
            finite_lows = np.array([])
            finite_highs = np.array([])

        brick_min = float(np.min(finite_lows)) if len(finite_lows) > 0 else 0.0
        brick_max = float(np.max(finite_highs)) if len(finite_highs) > 0 else 0.0
        if np.isfinite(brick_min) and np.isfinite(brick_max):
            if brick_max <= brick_min:
                pad = max(abs(brick_max) * 0.01, 0.05)
            else:
                pad = (brick_max - brick_min) * 0.02
            self.brickPlot.setYRange(brick_min - pad, brick_max + pad, padding=0)
        else:
            self.brickPlot.enableAutoRange(axis="y", enable=True)

        return brick_values

    def _update_kdj_panel(self, x, h, l, c):
        self.kdjPlot.clear()
        self.kdjPlot.addItem(self.kdjKCurve)
        self.kdjPlot.addItem(self.kdjDCurve)
        self.kdjPlot.addItem(self.kdjJCurve)
        self.kdjPlot.addItem(self.kdjLowLine, ignoreBounds=True)
        self.kdjLowLine.setPos(20)
        self.kdjPlot.addItem(self.kdjMidLine, ignoreBounds=True)
        self.kdjMidLine.setPos(50)
        self.kdjPlot.addItem(self.kdjHighLine, ignoreBounds=True)
        self.kdjHighLine.setPos(80)
        self.kdjPlot.addItem(self.kdjVLine, ignoreBounds=True)
        self.kdjVLine.show()

        kdj_result = compute_kdj_indicator(h, l, c)
        self._kdj_k_values = kdj_result["k"]
        self._kdj_d_values = kdj_result["d"]
        self._kdj_j_values = kdj_result["j"]
        self.kdjKCurve.setData(x, self._kdj_k_values)
        self.kdjDCurve.setData(x, self._kdj_d_values)
        self.kdjJCurve.setData(x, self._kdj_j_values)


        kdj_stack = np.concatenate([
            self._kdj_k_values[np.isfinite(self._kdj_k_values)],
            self._kdj_d_values[np.isfinite(self._kdj_d_values)],
            self._kdj_j_values[np.isfinite(self._kdj_j_values)],
        ])
        if len(kdj_stack) > 0:
            kdj_min = float(np.min(kdj_stack))
            kdj_max = float(np.max(kdj_stack))
            kdj_min = min(kdj_min, 0.0)
            kdj_max = max(kdj_max, 100.0)
            if kdj_max <= kdj_min:
                kdj_pad = max(abs(kdj_max) * 0.03, 1.0)
            else:
                kdj_pad = max((kdj_max - kdj_min) * 0.06, 1.0)
            self.kdjPlot.setYRange(kdj_min - kdj_pad, kdj_max + kdj_pad, padding=0)
        else:
            self.kdjPlot.setYRange(-5, 105, padding=0)

    def _update_needle20_panel(self, x, h, l, c):
        self.needle20Plot.clear()
        self.needle20Plot.addItem(self.needle20ShortCurve)
        self.needle20Plot.addItem(self.needle20MidCurve)
        self.needle20Plot.addItem(self.needle20LongCurve)
        self.needle20Plot.addItem(self.needle20LowLine, ignoreBounds=True)
        self.needle20LowLine.setPos(20)
        self.needle20Plot.addItem(self.needle20HighLine, ignoreBounds=True)
        self.needle20HighLine.setPos(80)
        self.needle20Plot.addItem(self.needle20VLine, ignoreBounds=True)
        self.needle20VLine.show()

        result = compute_needle20_indicator(h, l, c)
        self._needle20_short_values = result["short"]
        self._needle20_mid_values = result["mid"]
        self._needle20_long_values = result["long"]
        self.needle20ShortCurve.setData(x, self._needle20_short_values)
        self.needle20MidCurve.setData(x, self._needle20_mid_values)
        self.needle20LongCurve.setData(x, self._needle20_long_values)

        all_vals = np.concatenate([
            self._needle20_short_values[np.isfinite(self._needle20_short_values)],
            self._needle20_mid_values[np.isfinite(self._needle20_mid_values)],
            self._needle20_long_values[np.isfinite(self._needle20_long_values)],
        ])
        if len(all_vals) > 0:
            n20_min = float(np.min(all_vals))
            n20_max = float(np.max(all_vals))
            n20_min = min(n20_min, 0.0)
            n20_max = max(n20_max, 100.0)
            n20_pad = max((n20_max - n20_min) * 0.06, 1.0)
            self.needle20Plot.setYRange(n20_min - n20_pad, n20_max + n20_pad, padding=0)
        else:
            self.needle20Plot.setYRange(-5, 105, padding=0)

    def _update_macd_panel(self, x, c):
        self.macdPlot.clear()
        self.macdPlot.addItem(self.macdDiffCurve)
        self.macdPlot.addItem(self.macdDeaCurve)
        self.macdPlot.addItem(self.macdZeroLine, ignoreBounds=True)
        self.macdZeroLine.setPos(0)
        self.macdPlot.addItem(self.macdVLine, ignoreBounds=True)
        self.macdVLine.show()

        result = compute_macd_indicator(c)
        self._macd_diff_values = result["diff"]
        self._macd_dea_values = result["dea"]
        self._macd_macd_values = result["macd"]
        cross_up = result["cross_up"]
        cross_down = result["cross_down"]

        self.macdDiffCurve.setData(x, self._macd_diff_values)
        self.macdDeaCurve.setData(x, self._macd_dea_values)

        macd_vals = self._macd_macd_values
        cross_mask = cross_up | cross_down
        normal_mask = ~cross_mask

        normal_pos = normal_mask & (macd_vals >= 0)
        normal_neg = normal_mask & (macd_vals < 0)
        if np.any(normal_pos):
            self.macdPlot.addItem(pg.BarGraphItem(
                x=x[normal_pos], height=macd_vals[normal_pos], width=0.3,
                brush=pg.mkBrush(220, 0, 0, 200), pen=None,
            ))
        if np.any(normal_neg):
            self.macdPlot.addItem(pg.BarGraphItem(
                x=x[normal_neg], height=macd_vals[normal_neg], width=0.3,
                brush=pg.mkBrush(0, 170, 0, 200), pen=None,
            ))

        cross_pos = cross_mask & (macd_vals >= 0)
        cross_neg = cross_mask & (macd_vals < 0)
        if np.any(cross_pos):
            self.macdPlot.addItem(pg.BarGraphItem(
                x=x[cross_pos], height=macd_vals[cross_pos], width=0.7,
                brush=pg.mkBrush(220, 0, 0, 200), pen=None,
            ))
        if np.any(cross_neg):
            self.macdPlot.addItem(pg.BarGraphItem(
                x=x[cross_neg], height=macd_vals[cross_neg], width=0.7,
                brush=pg.mkBrush(0, 170, 0, 200), pen=None,
            ))

        all_vals = np.concatenate([
            self._macd_diff_values[np.isfinite(self._macd_diff_values)],
            self._macd_dea_values[np.isfinite(self._macd_dea_values)],
            macd_vals[np.isfinite(macd_vals)],
        ])
        if len(all_vals) > 0:
            macd_min = float(np.min(all_vals))
            macd_max = float(np.max(all_vals))
            macd_pad = max((macd_max - macd_min) * 0.06, 0.1)
            self.macdPlot.setYRange(macd_min - macd_pad, macd_max + macd_pad, padding=0)
