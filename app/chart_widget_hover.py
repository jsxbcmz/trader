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


class HoverMixin:
    def _update_indicator_label_values(self, idx: int):
        if idx < 0 or idx >= len(self._short_trend_values) or idx >= len(self._long_short_values):
            return

        text = build_indicator_label_html(
            float(self._short_trend_values[idx]),
            float(self._long_short_values[idx]),
        )
        self.indicatorLabel.setText(text)
        self.indicatorLabel.adjustSize()
        self.indicatorLabel.show()

    def _update_brick_delta_label(self, idx: int, brick_values: np.ndarray | None = None):
        if self._df is None or idx < 0:
            self.brickDeltaLabel.hide()
            return

        if brick_values is None:
            brick_values = self._brick_values

        if idx >= len(brick_values):
            self.brickDeltaLabel.hide()
            return

        current = float(brick_values[idx])
        if idx > 0 and np.isfinite(current):
            prev = float(brick_values[idx - 1])
            diff = current - prev if np.isfinite(prev) else float("nan")
        else:
            diff = float("nan")

        self.brickDeltaLabel.setText(build_brick_delta_label_html(diff))
        self.brickDeltaLabel.adjustSize()
        self.brickDeltaLabel.show()

    def _update_kdj_label_values(self, idx: int):
        if idx < 0 or idx >= len(self._kdj_k_values) or idx >= len(self._kdj_d_values) or idx >= len(self._kdj_j_values):
            self.kdjLabel.hide()
            return

        text = build_kdj_label_html(
            float(self._kdj_k_values[idx]),
            float(self._kdj_d_values[idx]),
            float(self._kdj_j_values[idx]),
        )
        self.kdjLabel.setText(text)
        self.kdjLabel.adjustSize()
        self.kdjLabel.show()

    def _update_needle20_label_values(self, idx: int):
        if (idx < 0 or idx >= len(self._needle20_short_values)
                or idx >= len(self._needle20_mid_values)
                or idx >= len(self._needle20_long_values)):
            self.needle20Label.hide()
            return

        text = build_needle20_label_html(
            float(self._needle20_short_values[idx]),
            float(self._needle20_mid_values[idx]),
            float(self._needle20_long_values[idx]),
        )
        self.needle20Label.setText(text)
        self.needle20Label.adjustSize()
        self.needle20Label.show()

    def _update_macd_label_values(self, idx: int):
        if (idx < 0 or idx >= len(self._macd_diff_values)
                or idx >= len(self._macd_dea_values)
                or idx >= len(self._macd_macd_values)):
            self.macdLabel.hide()
            return

        text = build_macd_label_html(
            float(self._macd_diff_values[idx]),
            float(self._macd_dea_values[idx]),
            float(self._macd_macd_values[idx]),
        )
        self.macdLabel.setText(text)
        self.macdLabel.adjustSize()
        self.macdLabel.show()

    def _hide_hover_artifacts(self):
        self.infoText.hide()
        self.yValueText.hide()
        self._last_hover_index = None
        self._last_hover_y = None
        self._last_y_text = None

    def _is_in_any_plot(self, pos) -> bool:
        if self.pricePlot.sceneBoundingRect().contains(pos):
            return True
        for chart_type in self._visible_sub_charts:
            if self._type_to_plot[chart_type].sceneBoundingRect().contains(pos):
                return True
        return False

    def _update_hover_for_index(self, idx: int, y: float, show_price_overlay: bool):
        snapped_x = float(idx)
        self._set_crosshair_x(snapped_x)

        if show_price_overlay:
            same_index = self._last_hover_index == idx
            should_update_y = (not same_index) or self._last_hover_y is None or abs(y - self._last_hover_y) >= 0.05
            if should_update_y:
                self.hLine.setPos(y)
                vb = self.pricePlot.getViewBox()
                (x0, x1), _ = vb.viewRange()
                x_range = x1 - x0
                x_pad = x_range * 0.01
                y_text = f"{y:.2f}"
                if y_text != self._last_y_text:
                    self.yValueText.setHtml(build_y_value_html(y_text))
                    self._last_y_text = y_text
                if snapped_x > x0 + x_range * 0.5:
                    self.yValueText.setAnchor((0, 0.5))
                    self.yValueText.setPos(x0 + x_pad, y)
                else:
                    self.yValueText.setAnchor((1, 0.5))
                    self.yValueText.setPos(x1 - x_pad, y)
                self._last_hover_y = y
            self.yValueText.show()
        else:
            self.yValueText.hide()
            self._last_hover_y = None
            self._last_y_text = None

        if self._last_hover_index == idx:
            return
        self._last_hover_index = idx

        self._update_indicator_label_values(idx)
        if SubChartType.BRICK in self._visible_sub_charts:
            self._update_brick_delta_label(idx, self._brick_values)
        if SubChartType.KDJ in self._visible_sub_charts:
            self._update_kdj_label_values(idx)
        if SubChartType.NEEDLE20 in self._visible_sub_charts:
            self._update_needle20_label_values(idx)
        if SubChartType.MACD in self._visible_sub_charts:
            self._update_macd_label_values(idx)

        row = self._df.iloc[idx]
        o = float(row["open"])
        c = float(row["close"])
        preclose = float(self._df.iloc[idx - 1]["close"]) if idx > 0 else float("nan")
        pct_chg = ((c - preclose) / preclose * 100.0) if (idx > 0 and preclose != 0) else float("nan")

        if show_price_overlay:
            self._update_info_box(idx, snapped_x, y)
        else:
            self.infoText.hide()

        self.onHover.emit(
            {
                "index": idx,
                "date": row["date"],
                "open": o,
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": c,
                "preclose": preclose,
                "pct_chg": pct_chg,
                "amount_yi": float(row.get("volume", np.nan)) / 1e4,
            }
        )

    def _set_crosshair_x(self, x: float):
        self.vLine.setPos(x)
        for chart_type in self._visible_sub_charts:
            self._type_to_vline[chart_type].setPos(x)
        self._update_crosshair_date(int(round(x)))

    def _update_crosshair_date(self, idx: int):
        """Update the crosshair date label position and text in the bottom date bar."""
        if not self._dates or idx < 0 or idx >= len(self._dates):
            self.crosshairDateLabel.hide()
            return

        date_str = self._dates[idx]
        self.crosshairDateLabel.setText(date_str)
        self.crosshairDateLabel.adjustSize()
        self.crosshairDateLabel.show()

        bottom_plot = self._type_to_plot.get(self._visible_sub_charts[-1], self.pricePlot)
        view_box = bottom_plot.getViewBox()
        scene_point = view_box.mapViewToScene(QtCore.QPointF(float(idx), 0))
        widget_point = bottom_plot.mapFromScene(scene_point)
        global_point = bottom_plot.mapToGlobal(widget_point)
        local_point = self.dateBar.mapFromGlobal(global_point)

        label_width = self.crosshairDateLabel.sizeHint().width()
        target_x = local_point.x() - label_width / 2
        bar_width = self.dateBar.width()
        target_x = max(FIXED_Y_AXIS_WIDTH, min(target_x, bar_width - label_width))
        self.crosshairDateLabel.move(int(target_x), 2)

    def _update_info_box(self, idx: int, x: float, y: float):
        if self._df is None or len(self._df) == 0:
            return
        if idx < 0 or idx >= len(self._df):
            return

        row = self._df.iloc[idx]
        d = row["date"]
        ds = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)

        c = float(row["close"])
        o = float(row.get("open", np.nan))
        h = float(row.get("high", np.nan))
        l = float(row.get("low", np.nan))
        amount_yi = float(row.get("volume", np.nan)) / 1e4
        turnover_rate = float(row.get("turnover_rate", np.nan))

        if idx > 0:
            preclose = float(self._df.iloc[idx - 1]["close"])
            pct = (c - preclose) / preclose * 100.0 if preclose != 0 else float("nan")
        else:
            pct = float("nan")

        text = build_info_box_html(ds, c, pct, amount_yi, turnover_rate,
                                   open_value=o, high_value=h, low_value=l)

        # Set HTML first so we can measure the actual rendered size
        self.infoText.setHtml(text)

        vb = self.pricePlot.getViewBox()
        (x0, x1), (y0, y1) = vb.viewRange()

        x_range = x1 - x0
        y_range = y1 - y0
        dx = x_range * 0.03
        dy = y_range * 0.02

        # Measure tooltip size in pixel space, then convert to data coordinates
        # so we can precisely check whether it would overflow the view boundary.
        br = self.infoText.boundingRect()
        tooltip_pixel_width = br.width()
        tooltip_pixel_height = br.height()

        view_pixel_rect = vb.screenGeometry()
        view_pixel_width = max(view_pixel_rect.width(), 1)
        view_pixel_height = max(view_pixel_rect.height(), 1)

        tooltip_data_width = tooltip_pixel_width / view_pixel_width * x_range
        tooltip_data_height = tooltip_pixel_height / view_pixel_height * y_range

        # Horizontal: prefer showing tooltip to the right of the cursor;
        # flip to the left only when it would overflow the right boundary.
        right_edge = x + dx + tooltip_data_width
        if right_edge > x1:
            px = x - dx
            self.infoText.setAnchor((1, 0))
        else:
            px = x + dx
            self.infoText.setAnchor((0, 0))

        # Vertical: prefer showing tooltip below the cursor;
        # flip upward only when it would overflow the bottom boundary.
        # Note: in pyqtgraph the Y axis may be inverted (y0 < y1 means
        # y0 is the bottom visually), so "below cursor" means towards y0.
        bottom_edge = y - dy - tooltip_data_height
        if bottom_edge < y0:
            # Not enough room below → show above cursor
            py = y + dy
            anchor_y = 1.0
        else:
            # Show below cursor
            py = y - dy
            anchor_y = 0.0

        # Re-check: if showing above also overflows the top, clamp to top
        if anchor_y == 1.0 and (py + tooltip_data_height) > y1:
            py = y1
        # If showing below overflows the bottom, clamp to bottom
        if anchor_y == 0.0 and (py - tooltip_data_height) < y0:
            py = y0 + tooltip_data_height

        current_anchor = self.infoText.anchor
        new_anchor_x = current_anchor.x()
        if current_anchor.y() != anchor_y:
            self.infoText.setAnchor((new_anchor_x, anchor_y))

        px = min(max(px, x0), x1)
        py = min(max(py, y0), y1)

        self.infoText.setPos(px, py)
        self.infoText.show()

    def _on_mouse_moved(self, source_plot, evt):
        if self._df is None or len(self._df) == 0 or self._loading_plot:
            return

        pos = evt[0]
        if not self._is_in_any_plot(pos):
            self._hide_hover_artifacts()
            return

        if not source_plot.sceneBoundingRect().contains(pos):
            return

        mousePoint = source_plot.plotItem.vb.mapSceneToView(pos)
        x = mousePoint.x()
        y = mousePoint.y()
        idx = int(round(x))

        if idx < 0 or idx >= len(self._df):
            self._hide_hover_artifacts()
            return

        self._update_hover_for_index(idx, y, show_price_overlay=(source_plot is self.pricePlot))
