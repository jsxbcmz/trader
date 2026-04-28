from __future__ import annotations

import bisect

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from .chart_primitives import CandlestickItem

MINI_CHART_DAYS = 90
MINI_CHART_HEIGHT = 120


class MiniCandleChart(QtWidgets.QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(MINI_CHART_HEIGHT)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._plot = pg.PlotWidget()
        self._plot.setViewportUpdateMode(QtWidgets.QGraphicsView.FullViewportUpdate)
        self._plot.setMouseEnabled(x=False, y=False)
        self._plot.hideButtons()
        self._plot.setMenuEnabled(False)

        for axis_name in ("left", "bottom"):
            axis = self._plot.getAxis(axis_name)
            axis.setStyle(showValues=False)
            axis.setTicks([])
            axis.setWidth(0) if axis_name == "left" else axis.setHeight(0)

        self._plot.showGrid(x=False, y=False)
        self._plot.setBackground("#1a1a1a")

        self._candle = CandlestickItem()
        self._plot.addItem(self._candle)

        self._vline = pg.InfiniteLine(angle=90, pen=pg.mkPen("w", width=1, style=QtCore.Qt.DashLine))
        self._vline.setZValue(10)
        self._plot.addItem(self._vline)
        self._vline.hide()

        layout.addWidget(self._plot)

        self._title_label = QtWidgets.QLabel(self._plot)
        self._title_label.setStyleSheet(
            "color: rgba(255, 255, 255, 0.6); background: transparent; "
            "border: none; padding: 0px; margin: 0px; font-size: 12px; font-weight: bold;"
        )
        self._title_label.move(6, 4)
        self._title_label.hide()

        self._price_label = QtWidgets.QLabel(self._plot)
        self._price_label.setStyleSheet(
            "color: rgba(255, 255, 255, 0.5); background: transparent; "
            "border: none; padding: 0px; margin: 0px; font-size: 11px;"
        )
        self._price_label.move(6, 22)
        self._price_label.hide()

        self._empty_label = QtWidgets.QLabel("暂无数据", self._plot)
        self._empty_label.setStyleSheet(
            "color: rgba(255, 255, 255, 0.3); background: transparent; "
            "border: none; font-size: 12px;"
        )
        self._empty_label.setAlignment(QtCore.Qt.AlignCenter)
        self._empty_label.hide()

        self._dates: list[str] = []
        self._date_keys: list[str] = []
        self._closes: np.ndarray = np.array([])
        self._pre_closes: np.ndarray = np.array([])
        self._highs: np.ndarray = np.array([])
        self._lows: np.ndarray = np.array([])
        self._last_price_html: str = ""
        self._hover_active = False

        self._plot.scene().sigMouseMoved.connect(self._on_mouse_moved)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._empty_label.isVisible():
            self._empty_label.setGeometry(0, 0, self.width(), self.height())

    def leaveEvent(self, event):
        super().leaveEvent(event)
        if self._hover_active:
            self._hover_active = False
            self._vline.hide()
            if self._last_price_html:
                self._price_label.setText(self._last_price_html)
                self._price_label.setTextFormat(QtCore.Qt.RichText)
                self._price_label.adjustSize()
                self._price_label.show()

    @staticmethod
    def _format_price_html(close: float, pre_close: float, date_str: str = "") -> str:
        pct = (close - pre_close) / pre_close * 100 if pre_close != 0 else 0
        color = "#ff4d4f" if pct >= 0 else "#00b050"
        prefix = f"<span style='color:rgba(255,255,255,0.45);'>{date_str}</span> " if date_str else ""
        return f"{prefix}<span style='color:{color};'>{close:.2f} ({pct:+.2f}%)</span>"

    def _on_mouse_moved(self, pos):
        if len(self._closes) == 0:
            return
        vb = self._plot.plotItem.vb
        mouse_point = vb.mapSceneToView(pos)
        x = mouse_point.x()
        i = int(round(x))
        n = len(self._closes)
        if 0 <= i < n:
            self._hover_active = True
            self._vline.setPos(i)
            self._vline.show()
            html = self._format_price_html(float(self._closes[i]), float(self._pre_closes[i]), self._dates[i])
            self._price_label.setText(html)
            self._price_label.setTextFormat(QtCore.Qt.RichText)
            self._price_label.adjustSize()
            self._price_label.show()
        elif self._hover_active:
            self._hover_active = False
            self._vline.hide()
            if self._last_price_html:
                self._price_label.setText(self._last_price_html)
                self._price_label.setTextFormat(QtCore.Qt.RichText)
                self._price_label.adjustSize()

    def sync_date_range(self, start_date: str, end_date: str):
        if not self._date_keys:
            return
        left = bisect.bisect_left(self._date_keys, start_date)
        right = bisect.bisect_right(self._date_keys, end_date) - 1
        left = max(0, left)
        right = min(right, len(self._date_keys) - 1)
        if left > right:
            return
        x0 = left - 0.5
        x1 = right + 0.5
        self._plot.setXRange(x0, x1, padding=0)
        vis_low = self._lows[left:right + 1]
        vis_high = self._highs[left:right + 1]
        if len(vis_low) == 0:
            return
        y_min = float(np.nanmin(vis_low))
        y_max = float(np.nanmax(vis_high))
        y_pad = (y_max - y_min) * 0.08 if y_max > y_min else 1.0
        self._plot.setYRange(y_min - y_pad, y_max + y_pad, padding=0)

    def set_data(self, df, title: str = ""):
        self._title_label.setText(title)
        self._title_label.adjustSize()
        self._title_label.show()
        self._vline.hide()
        self._hover_active = False

        if df is None or df.empty or len(df) < 2:
            self._candle.setData(np.empty((0, 5)), is_up=np.array([], dtype=bool))
            self._price_label.hide()
            self._empty_label.setGeometry(0, 0, self.width(), self.height())
            self._empty_label.show()
            self._dates = []
            self._date_keys = []
            self._closes = np.array([])
            self._pre_closes = np.array([])
            self._highs = np.array([])
            self._lows = np.array([])
            self._last_price_html = ""
            return

        self._empty_label.hide()

        full = df.reset_index(drop=True)
        n = len(full)
        x = np.arange(n, dtype=float)
        o = full["open"].to_numpy(float)
        h = full["high"].to_numpy(float)
        l = full["low"].to_numpy(float)
        c = full["close"].to_numpy(float)

        pre_close = np.empty_like(c)
        pre_close[0] = o[0]
        pre_close[1:] = c[:-1]
        is_up = c >= pre_close

        self._candle.setData(np.column_stack([x, o, h, l, c]), is_up=is_up)

        tail_start = max(0, n - MINI_CHART_DAYS)
        vis_l = l[tail_start:]
        vis_h = h[tail_start:]
        y_min = float(np.nanmin(vis_l))
        y_max = float(np.nanmax(vis_h))
        y_pad = (y_max - y_min) * 0.08 if y_max > y_min else 1.0
        self._plot.setXRange(tail_start - 0.5, n - 0.5, padding=0)
        self._plot.setYRange(y_min - y_pad, y_max + y_pad, padding=0)

        self._dates = full["date"].dt.strftime("%m-%d").tolist()
        self._date_keys = full["date"].dt.strftime("%Y-%m-%d").tolist()
        self._closes = c
        self._pre_closes = pre_close
        self._highs = h
        self._lows = l

        self._last_price_html = self._format_price_html(float(c[-1]), float(pre_close[-1]))
        self._price_label.setText(self._last_price_html)
        self._price_label.setTextFormat(QtCore.Qt.RichText)
        self._price_label.adjustSize()
        self._price_label.show()

    def clear_data(self):
        self._candle.setData(np.empty((0, 5)), is_up=np.array([], dtype=bool))
        self._title_label.hide()
        self._price_label.hide()
        self._empty_label.hide()
        self._vline.hide()
        self._dates = []
        self._date_keys = []
        self._closes = np.array([])
        self._pre_closes = np.array([])
        self._highs = np.array([])
        self._lows = np.array([])
        self._last_price_html = ""
        self._hover_active = False
