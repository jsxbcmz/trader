from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from .chart_indicators import (
    ZX_MULTI_PERIODS,
    calc_brick_threshold_price,
    compute_brick_indicator,
    compute_kdj_indicator,
    compute_zx_long_short,
    compute_zx_short_trend,
)
from .chart_interaction import window_width_for_days
from .chart_layout import (
    FIXED_Y_AXIS_WIDTH,
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

    stopRequested = QtCore.Signal()

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

        self.stopButton = QtWidgets.QPushButton("停止")
        self.closeButton = QtWidgets.QPushButton("关闭")
        self.closeButton.setEnabled(False)

        btnLayout = QtWidgets.QHBoxLayout()
        btnLayout.addStretch(1)
        btnLayout.addWidget(self.stopButton)
        btnLayout.addWidget(self.closeButton)

        layout.addWidget(self.progressLabel)
        layout.addWidget(self.progressBar)
        layout.addWidget(self.currentLabel)
        layout.addWidget(self.statsLabel)
        layout.addStretch(1)
        layout.addLayout(btnLayout)

        self.stopButton.clicked.connect(self._on_stop_clicked)
        self.closeButton.clicked.connect(self.accept)

    def _on_stop_clicked(self):
        self.stopButton.setEnabled(False)
        self.progressLabel.setText("正在停止，请稍候...")
        self.stopRequested.emit()

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
        self.stopButton.setEnabled(False)
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
        self._brick_green_threshold_items: list[pg.GraphicsObject] = []
        self._kdj_k_values = np.array([])
        self._kdj_d_values = np.array([])
        self._kdj_j_values = np.array([])
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
        self.priceViewBox = bundle.price_viewbox
        self.volViewBox = bundle.vol_viewbox
        self.brickViewBox = bundle.brick_viewbox
        self.kdjViewBox = bundle.kdj_viewbox
        self.pricePlot = bundle.price_plot
        self.volPlot = bundle.vol_plot
        self.brickPlot = bundle.brick_plot
        self.kdjPlot = bundle.kdj_plot

        self.chartContainer, self.chartLayout, layout, date_bar_items = create_chart_layout(
            self,
            self.pricePlot,
            self.volPlot,
            self.brickPlot,
            self.kdjPlot,
        )
        self.dateBar = date_bar_items.date_bar
        self.leftDateLabel = date_bar_items.left_date_label
        self.rightDateLabel = date_bar_items.right_date_label
        self.crosshairDateLabel = date_bar_items.crosshair_date_label

        price_items = create_price_items(self.pricePlot)
        self.candleItem = price_items.candle_item
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

        view_box = self.kdjPlot.getViewBox()
        scene_point = view_box.mapViewToScene(QtCore.QPointF(float(idx), 0))
        widget_point = self.kdjPlot.mapFromScene(scene_point)
        global_point = self.kdjPlot.mapToGlobal(widget_point)
        local_point = self.dateBar.mapFromGlobal(global_point)

        label_width = self.crosshairDateLabel.sizeHint().width()
        target_x = local_point.x() - label_width / 2
        bar_width = self.dateBar.width()
        target_x = max(FIXED_Y_AXIS_WIDTH, min(target_x, bar_width - label_width))
        self.crosshairDateLabel.move(int(target_x), 2)

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

    def _update_volume_panel(self, x, o, c, amount_yi):
        self.volPlot.clear()
        self.volPlot.addItem(self.volVLine, ignoreBounds=True)
        self.volVLine.show()
        self._amount_yi_values = amount_yi

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
        self._update_price_guide_lines(ymin, ymax)

        if len(self._amount_yi_values) > 0:
            visible_amount = self._amount_yi_values[left:right + 1]
            finite_amount = visible_amount[np.isfinite(visible_amount)]
            if len(finite_amount) > 0:
                vol_max = float(np.max(finite_amount))
                vol_pad = vol_max * 0.02
                self.volPlot.setYRange(0, vol_max + vol_pad, padding=0)

        if len(self._brick_values) > 0:
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

            x, o, h, l, c, amount_yi = self._prepare_daily_arrays(df)
            self._update_price_panel(x, o, h, l, c)
            self._update_volume_panel(x, o, c, amount_yi)
            brick_values = self._update_brick_panel(x, h, l, c)
            self._update_brick_green_thresholds(h, l, c, brick_values)
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
