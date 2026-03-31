from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtGui


class DateAxisItem(pg.AxisItem):
    """X axis that shows YYYY-MM-DD labels by index."""

    def __init__(self, dates, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._dates = dates  # list[str]

    def tickStrings(self, values, scale, spacing):
        out = []
        n = len(self._dates)
        for v in values:
            i = int(round(v))
            if 0 <= i < n:
                out.append(self._dates[i])
            else:
                out.append("")
        return out


class CandlestickItem(pg.GraphicsObject):
    """Simple candlestick item.

    Data: ndarray of rows [x, open, high, low, close]
    """

    BODY_WIDTH = 0.6

    def __init__(self):
        super().__init__()
        self._data: np.ndarray | None = None
        self._up_pen = pg.mkPen((220, 0, 0))
        self._up_brush = pg.mkBrush((220, 0, 0))
        self._down_pen = pg.mkPen((0, 170, 0))
        self._down_brush = pg.mkBrush((0, 170, 0))

    def setData(self, data: np.ndarray):
        self._data = data
        self.prepareGeometryChange()
        self.update()

    def paint(self, p, *args):
        if self._data is None or len(self._data) == 0:
            return

        for x, o, h, l, c in self._data:
            if np.isnan([o, h, l, c]).any():
                continue

            up = c >= o
            pen = self._up_pen if up else self._down_pen
            brush = self._up_brush if up else self._down_brush

            p.setPen(pen)
            p.drawLine(QtCore.QPointF(x, l), QtCore.QPointF(x, h))

            p.setPen(pen)
            p.setBrush(brush)
            top = max(o, c)
            bottom = min(o, c)
            rect_h = top - bottom
            if rect_h == 0:
                rect_h = 0.001
            body_width = self.BODY_WIDTH
            half_width = body_width / 2
            p.drawRect(QtCore.QRectF(x - half_width, bottom, body_width, rect_h))

    def boundingRect(self):
        if self._data is None or len(self._data) == 0:
            return QtCore.QRectF()

        xmin = float(np.min(self._data[:, 0])) - 1
        xmax = float(np.max(self._data[:, 0])) + 1
        ymin = float(np.min(self._data[:, 3]))
        ymax = float(np.max(self._data[:, 2]))
        return QtCore.QRectF(xmin, ymin, xmax - xmin, ymax - ymin)


class BrickDeltaItem(pg.GraphicsObject):
    """Stickline-style brick segment item."""

    BODY_WIDTH = 0.6

    def __init__(self):
        super().__init__()
        self._data: np.ndarray | None = None
        self._up_pen = pg.mkPen((220, 0, 0), width=0)
        self._up_brush = pg.mkBrush((220, 0, 0, 210))
        self._down_pen = pg.mkPen((0, 170, 0), width=0)
        self._down_brush = pg.mkBrush((0, 170, 0, 210))

    def setData(self, data: np.ndarray):
        self.prepareGeometryChange()
        self._data = data
        self.update()

    def paint(self, p, *args):
        if self._data is None or len(self._data) == 0:
            return

        body_width = self.BODY_WIDTH
        half_width = body_width / 2
        p.setRenderHint(QtGui.QPainter.Antialiasing, False)

        for x, prev_brick, current_brick in self._data:
            if not np.isfinite(current_brick):
                continue

            start = float(prev_brick) if np.isfinite(prev_brick) else 0.0
            end = float(current_brick)

            if abs(end - start) <= 1e-12:
                continue

            up = end > start
            pen = self._up_pen if up else self._down_pen
            brush = self._up_brush if up else self._down_brush

            low = min(start, end)
            high = max(start, end)
            height = high - low
            if height <= 1e-12:
                height = 0.001

            p.setPen(pen)
            p.setBrush(brush)
            p.drawRect(QtCore.QRectF(x - half_width, low, body_width, height))

    def boundingRect(self):
        if self._data is None or len(self._data) == 0:
            return QtCore.QRectF()

        xs = self._data[:, 0]
        prevs = self._data[:, 1]
        currents = self._data[:, 2]

        xmin = float(np.min(xs)) - 1
        xmax = float(np.max(xs)) + 1

        lows = []
        highs = []
        for prev_brick, current_brick in zip(prevs, currents):
            if not np.isfinite(current_brick):
                continue
            start = float(prev_brick) if np.isfinite(prev_brick) else 0.0
            end = float(current_brick)
            lows.append(min(start, end))
            highs.append(max(start, end))

        if not lows or not highs:
            ymin, ymax = -1.0, 1.0
        else:
            ymin = float(min(lows))
            ymax = float(max(highs))
        return QtCore.QRectF(xmin, ymin, xmax - xmin, ymax - ymin)
