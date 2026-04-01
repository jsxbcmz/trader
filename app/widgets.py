from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from .chart_indicators import (
    ZX_MULTI_PERIODS,
    compute_brick_indicator,
    compute_kdj_indicator,
    compute_zx_long_short,
    compute_zx_short_trend,
)
from .chart_interaction import window_width_for_days
from .chart_layout import (
    create_brick_items,
    create_chart_layout,
    create_kdj_items,
    create_plot_bundle,
    create_price_items,
    create_volume_items,
)
from .chart_overlays import (
    build_brick_delta_label_html,
    build_indicator_label_html,
    build_info_box_html,
    build_kdj_label_html,
    build_y_value_html,
)
from .chart_primitives import CandlestickItem
from .chart_ranges import clamp_xrange, padded_min_max, visible_index_range


class UpdateProgressDialog(QtWidgets.QDialog):
    cancelRequested = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("更新进度")
        self.resize(560, 380)
        layout = QtWidgets.QVBoxLayout(self)

        self.progressLabel = QtWidgets.QLabel("准备开始更新...")
        self.progressBar = QtWidgets.QProgressBar()
        self.progressBar.setTextVisible(True)
        self.currentLabel = QtWidgets.QLabel("当前股票：-")
        self.detailLabel = QtWidgets.QLabel("阶段：等待开始")
        self.statsLabel = QtWidgets.QLabel("成功: 0  跳过: 0  失败: 0")
        self.logEdit = QtWidgets.QPlainTextEdit()
        self.logEdit.setReadOnly(True)
        self.cancelButton = QtWidgets.QPushButton("取消")
        self.closeButton = QtWidgets.QPushButton("关闭")
        self.closeButton.setEnabled(False)

        btnLayout = QtWidgets.QHBoxLayout()
        btnLayout.addStretch(1)
        btnLayout.addWidget(self.cancelButton)
        btnLayout.addWidget(self.closeButton)

        layout.addWidget(self.progressLabel)
        layout.addWidget(self.progressBar)
        layout.addWidget(self.currentLabel)
        layout.addWidget(self.detailLabel)
        layout.addWidget(self.statsLabel)
        layout.addWidget(self.logEdit, 1)
        layout.addLayout(btnLayout)

        self.cancelButton.clicked.connect(self.cancelRequested.emit)
        self.closeButton.clicked.connect(self.accept)

    def update_progress(self, payload: dict):
        current = int(payload.get("current", 0) or 0)
        total = max(int(payload.get("total", 0) or 0), 1)
        symbol = payload.get("symbol", "")
        name = payload.get("name", "")
        stage = payload.get("stage", "")
        message = payload.get("message", "")
        success = int(payload.get("success", 0) or 0)
        skipped = int(payload.get("skipped", 0) or 0)
        failed = int(payload.get("failed", 0) or 0)
        phase_text = str(payload.get("phase_text", "") or "").strip()
        stage_text = str(payload.get("stage_text", "") or "").strip() or stage or "处理中"

        self.progressBar.setMaximum(total)
        self.progressBar.setValue(min(current, total))
        self.progressLabel.setText(f"进度：{current} / {total}")
        self.currentLabel.setText(f"当前股票：{symbol or '-'} {name}".rstrip())
        self.detailLabel.setText(f"阶段：{stage_text}")
        self.statsLabel.setText(f"成功: {success}  跳过: {skipped}  失败: {failed}")
        if symbol or message or phase_text:
            line = f"[{current}/{total}] {symbol} {name}"
            if phase_text:
                line += f" | {phase_text}"
            else:
                line += f" | {stage_text}"
            if message:
                line += f" - {message}"
            self.logEdit.appendPlainText(line.strip())

    def mark_finished(self):
        self.cancelButton.setEnabled(False)
        self.closeButton.setEnabled(True)

    def mark_cancel_requested(self):
        self.cancelButton.setEnabled(False)
        self.detailLabel.setText("阶段：正在请求取消，请稍候...")
        self.logEdit.appendPlainText("已请求取消，等待当前股票处理结束...")


class ScreeningProgressDialog(QtWidgets.QDialog):
    """选股进度弹窗"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选股进度")
        self.resize(480, 320)
        layout = QtWidgets.QVBoxLayout(self)

        self.progressLabel = QtWidgets.QLabel("准备开始选股...")
        self.progressBar = QtWidgets.QProgressBar()
        self.progressBar.setTextVisible(True)
        self.currentLabel = QtWidgets.QLabel("当前股票：-")
        self.statsLabel = QtWidgets.QLabel("已处理: 0  命中: 0  错误: 0")
        self.closeButton = QtWidgets.QPushButton("关闭")
        self.closeButton.setEnabled(False)

        layout.addWidget(self.progressLabel)
        layout.addWidget(self.progressBar)
        layout.addWidget(self.currentLabel)
        layout.addWidget(self.statsLabel)
        layout.addStretch(1)
        layout.addWidget(self.closeButton)

        self.closeButton.clicked.connect(self.accept)

    def update_progress(self, payload: dict):
        current = int(payload.get("current", 0) or 0)
        total = max(int(payload.get("total", 0) or 0), 1)
        symbol = payload.get("symbol", "")
        matched = int(payload.get("matched", 0) or 0)
        errors = int(payload.get("errors", 0) or 0)

        self.progressBar.setMaximum(total)
        self.progressBar.setValue(min(current, total))
        self.progressLabel.setText(f"选股进度：{current} / {total}")
        self.currentLabel.setText(f"当前股票：{symbol or '-'}")
        self.statsLabel.setText(f"已处理: {current}  命中: {matched}  错误: {errors}")

    def mark_finished(self, summary: str = ""):
        self.closeButton.setEnabled(True)
        if summary:
            self.progressLabel.setText(summary)


class StockChartWidget(QtWidgets.QWidget):
    """Daily candlestick + custom indicator lines + amount bars + crosshair."""

    DEFAULT_MIN_VISIBLE_DAYS = 30
    DEFAULT_MAX_VISIBLE_DAYS = 150

    onHover = QtCore.Signal(dict)

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
        self._kdj_k_values = np.array([])
        self._kdj_d_values = np.array([])
        self._kdj_j_values = np.array([])
        self._last_hover_index: int | None = None
        self._last_hover_y: float | None = None
        self._last_visible_range_indices: tuple[int, int] | None = None
        self._low_values = np.array([])
        self._high_values = np.array([])
        self._last_y_text: str | None = None
        self._item_half_width = CandlestickItem.BODY_WIDTH / 2
        self._right_view_padding = 1.5
        self._min_visible_days = self.DEFAULT_MIN_VISIBLE_DAYS
        self._max_visible_days = self.DEFAULT_MAX_VISIBLE_DAYS

        bundle = create_plot_bundle(self)
        self.priceAxis = bundle["priceAxis"]
        self.volAxis = bundle["volAxis"]
        self.brickAxis = bundle["brickAxis"]
        self.kdjAxis = bundle["kdjAxis"]
        self.priceViewBox = bundle["priceViewBox"]
        self.volViewBox = bundle["volViewBox"]
        self.brickViewBox = bundle["brickViewBox"]
        self.kdjViewBox = bundle["kdjViewBox"]
        self.pricePlot = bundle["pricePlot"]
        self.volPlot = bundle["volPlot"]
        self.brickPlot = bundle["brickPlot"]
        self.kdjPlot = bundle["kdjPlot"]

        self.chartContainer, self.chartLayout, layout = create_chart_layout(
            self,
            self.pricePlot,
            self.volPlot,
            self.brickPlot,
            self.kdjPlot,
        )

        price_items = create_price_items(self.pricePlot)
        self.candleItem = price_items["candleItem"]
        self.zx_short_trend = price_items["zx_short_trend"]
        self.zx_long_short = price_items["zx_long_short"]
        self.vLine = price_items["vLine"]
        self.hLine = price_items["hLine"]
        self.infoText = price_items["infoText"]
        self.yValueText = price_items["yValueText"]
        self.indicatorLabel = price_items["indicatorLabel"]

        volume_items = create_volume_items(self.volPlot)
        self.volVLine = volume_items["volVLine"]

        brick_items = create_brick_items(self.brickPlot)
        self.brickDeltaItem = brick_items["brickDeltaItem"]
        self.brickZeroLine = brick_items["brickZeroLine"]
        self.brickVLine = brick_items["brickVLine"]
        self.brickDeltaLabel = brick_items["brickDeltaLabel"]

        kdj_items = create_kdj_items(self.kdjPlot)
        self.kdjKCurve = kdj_items["kdjKCurve"]
        self.kdjDCurve = kdj_items["kdjDCurve"]
        self.kdjJCurve = kdj_items["kdjJCurve"]
        self.kdjLowLine = kdj_items["kdjLowLine"]
        self.kdjMidLine = kdj_items["kdjMidLine"]
        self.kdjHighLine = kdj_items["kdjHighLine"]
        self.kdjVLine = kdj_items["kdjVLine"]
        self.kdjLabel = kdj_items["kdjLabel"]

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

        # Clamp panning/zooming to shared data range no matter which subplot initiates it
        for plot in (self.pricePlot, self.volPlot, self.brickPlot, self.kdjPlot):
            plot.getViewBox().sigRangeChanged.connect(self._clamp_xrange)

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

    def _hide_hover_artifacts(self):
        self.infoText.hide()
        self.yValueText.hide()
        self._last_hover_index = None
        self._last_hover_y = None
        self._last_y_text = None

    def _is_in_any_plot(self, pos) -> bool:
        return (
            self.pricePlot.sceneBoundingRect().contains(pos)
            or self.volPlot.sceneBoundingRect().contains(pos)
            or self.brickPlot.sceneBoundingRect().contains(pos)
            or self.kdjPlot.sceneBoundingRect().contains(pos)
        )

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
        self._update_brick_delta_label(idx, self._brick_values)
        self._update_kdj_label_values(idx)

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
        for axis in (self.priceAxis, self.volAxis, self.brickAxis, self.kdjAxis):
            self._set_axes_dates(axis)
        for plot in (self.pricePlot, self.volPlot, self.brickPlot, self.kdjPlot):
            self._reset_axis_picture(plot)

    def _set_crosshair_x(self, x: float):
        self.vLine.setPos(x)
        self.volVLine.setPos(x)
        self.brickVLine.setPos(x)
        self.kdjVLine.setPos(x)

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

        for plot in (self.pricePlot, self.volPlot, self.brickPlot, self.kdjPlot):
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

    def _prepare_daily_arrays(self, df):
        x = np.arange(len(df), dtype=float)
        o = df["open"].to_numpy(float)
        h = df["high"].to_numpy(float)
        l = df["low"].to_numpy(float)
        c = df["close"].to_numpy(float)
        amount_yi = df["volume"].to_numpy(float) / 1e4
        return x, o, h, l, c, amount_yi

    def _update_price_panel(self, x, o, h, l, c):
        self._low_values = l
        self._high_values = h
        self.candleItem.setData(np.column_stack([x, o, h, l, c]))

        short_trend = compute_zx_short_trend(c)
        long_short = compute_zx_long_short(c, periods=ZX_MULTI_PERIODS)
        self._short_trend_values = short_trend
        self._long_short_values = long_short
        self.zx_short_trend.setData(x, short_trend)
        self.zx_long_short.setData(x, long_short)

    def _update_volume_panel(self, x, o, c, amount_yi):
        self.volPlot.clear()
        self.volPlot.addItem(self.volVLine, ignoreBounds=True)
        self.volVLine.show()

        up_mask = c >= o
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

        self.volPlot.setLabel("left", "成交额(亿)")
        self.volPlot.enableAutoRange(axis="y", enable=True)

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

        self.brickPlot.setLabel("left", "砖型图")

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
                pad = max(abs(brick_max) * 0.03, 0.1)
            else:
                pad = max((brick_max - brick_min) * 0.06, 0.1)
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

        self.kdjPlot.setLabel("left", "KDJ")

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
        self.infoText.hide()
        self.yValueText.hide()

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
        visible_low = self._low_values[left:right + 1]
        visible_high = self._high_values[left:right + 1]
        y_range = padded_min_max(visible_low, visible_high)
        if y_range is None:
            return

        ymin, ymax = y_range
        self.pricePlot.setYRange(ymin, ymax, padding=0)

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

            x, o, h, l, c, amount_yi = self._prepare_daily_arrays(df)
            self._update_price_panel(x, o, h, l, c)
            self._update_volume_panel(x, o, c, amount_yi)
            brick_values = self._update_brick_panel(x, h, l, c)
            self._update_kdj_panel(x, h, l, c)
            self._reset_initial_view(df, brick_values)
        finally:
            self._loading_plot = False

    def _update_info_box(self, idx: int, x: float, y: float):
        if self._df is None or len(self._df) == 0:
            return
        if idx < 0 or idx >= len(self._df):
            return

        row = self._df.iloc[idx]
        d = row["date"]
        ds = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)

        c = float(row["close"])
        amount_yi = float(row.get("volume", np.nan)) / 1e4

        if idx > 0:
            preclose = float(self._df.iloc[idx - 1]["close"])
            pct = (c - preclose) / preclose * 100.0 if preclose != 0 else float("nan")
        else:
            pct = float("nan")

        text = build_info_box_html(ds, c, pct, amount_yi)

        vb = self.pricePlot.getViewBox()
        (x0, x1), (y0, y1) = vb.viewRange()

        x_range = x1 - x0
        y_range = y1 - y0
        dx = x_range * 0.03
        dy = y_range * 0.06

        if x > x0 + x_range * 0.72:
            px = x - dx
            self.infoText.setAnchor((1, 0))
        else:
            px = x + dx
            self.infoText.setAnchor((0, 0))

        if y > y0 + y_range * 0.78:
            py = y - dy
        else:
            py = y + dy

        px = min(max(px, x0), x1)
        py = min(max(py, y0), y1)

        self.infoText.setHtml(text)
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
