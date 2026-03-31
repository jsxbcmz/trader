from __future__ import annotations

import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from .chart_primitives import BrickDeltaItem, CandlestickItem, DateAxisItem
from .chart_interaction import StockChartViewBox


def configure_plot_widget(plot: pg.PlotWidget):
    plot.setViewportUpdateMode(QtWidgets.QGraphicsView.FullViewportUpdate)
    plot.showGrid(x=True, y=True, alpha=0.25)
    plot.setMouseEnabled(x=True, y=False)


def create_plot_bundle(owner):
    price_axis = DateAxisItem([], orientation="bottom")
    vol_axis = DateAxisItem([], orientation="bottom")
    brick_axis = DateAxisItem([], orientation="bottom")
    kdj_axis = DateAxisItem([], orientation="bottom")

    price_viewbox = StockChartViewBox(owner)
    vol_viewbox = StockChartViewBox(owner)
    brick_viewbox = StockChartViewBox(owner)
    kdj_viewbox = StockChartViewBox(owner)

    price_plot = pg.PlotWidget(axisItems={"bottom": price_axis}, viewBox=price_viewbox)
    vol_plot = pg.PlotWidget(axisItems={"bottom": vol_axis}, viewBox=vol_viewbox)
    brick_plot = pg.PlotWidget(axisItems={"bottom": brick_axis}, viewBox=brick_viewbox)
    kdj_plot = pg.PlotWidget(axisItems={"bottom": kdj_axis}, viewBox=kdj_viewbox)

    for plot in (price_plot, vol_plot, brick_plot, kdj_plot):
        configure_plot_widget(plot)

    price_plot.setXLink(vol_plot)
    brick_plot.setXLink(price_plot)
    kdj_plot.setXLink(price_plot)

    return {
        "priceAxis": price_axis,
        "volAxis": vol_axis,
        "brickAxis": brick_axis,
        "kdjAxis": kdj_axis,
        "priceViewBox": price_viewbox,
        "volViewBox": vol_viewbox,
        "brickViewBox": brick_viewbox,
        "kdjViewBox": kdj_viewbox,
        "pricePlot": price_plot,
        "volPlot": vol_plot,
        "brickPlot": brick_plot,
        "kdjPlot": kdj_plot,
    }


def create_chart_layout(owner, price_plot, vol_plot, brick_plot, kdj_plot):
    chart_container = QtWidgets.QWidget()
    chart_layout = QtWidgets.QVBoxLayout(chart_container)
    chart_layout.setContentsMargins(0, 0, 16, 0)
    chart_layout.setSpacing(0)
    chart_layout.addWidget(price_plot, 3)
    chart_layout.addWidget(vol_plot, 1)
    chart_layout.addWidget(brick_plot, 1)
    chart_layout.addWidget(kdj_plot, 1)

    layout = QtWidgets.QVBoxLayout(owner)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(chart_container)

    return chart_container, chart_layout, layout


def create_price_items(price_plot):
    candle_item = CandlestickItem()
    price_plot.addItem(candle_item)
    zx_short_trend = price_plot.plot(
        pen=pg.mkPen((255, 255, 255), width=1.5),
        name="知行短期趋势线",
    )
    zx_long_short = price_plot.plot(
        pen=pg.mkPen((255, 215, 0), width=1.5),
        name="知行多空线",
    )

    v_line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen((200, 200, 200), width=1))
    h_line = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen((200, 200, 200), width=1))
    price_plot.addItem(v_line, ignoreBounds=True)
    price_plot.addItem(h_line, ignoreBounds=True)

    info_text = pg.TextItem(anchor=(0, 0), color=(255, 255, 255), fill=pg.mkBrush(0, 0, 0, 200), border=pg.mkPen(255, 255, 0, 220))
    info_text.setZValue(1_000)
    price_plot.addItem(info_text)
    info_text.hide()

    y_value_text = pg.TextItem(anchor=(1, 0.5), color=(255, 255, 255), fill=pg.mkBrush(20, 20, 20, 220), border=pg.mkPen(220, 220, 220, 220))
    y_value_text.setZValue(1_000)
    price_plot.addItem(y_value_text)
    y_value_text.hide()

    indicator_label = QtWidgets.QLabel(price_plot)
    indicator_label.setStyleSheet("color: white; background: transparent; border: none; padding: 0px; margin: 0px;")
    indicator_label.setTextFormat(QtCore.Qt.RichText)
    indicator_label.move(40, 2)
    indicator_label.hide()

    return {
        "candleItem": candle_item,
        "zx_short_trend": zx_short_trend,
        "zx_long_short": zx_long_short,
        "vLine": v_line,
        "hLine": h_line,
        "infoText": info_text,
        "yValueText": y_value_text,
        "indicatorLabel": indicator_label,
    }


def create_volume_items(vol_plot):
    vol_v_line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen((200, 200, 200), width=1))
    vol_plot.addItem(vol_v_line, ignoreBounds=True)
    vol_v_line.hide()
    return {"volVLine": vol_v_line}


def create_brick_items(brick_plot):
    brick_delta_item = BrickDeltaItem()
    brick_plot.addItem(brick_delta_item)

    brick_zero_line = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen((120, 120, 120), width=1))
    brick_plot.addItem(brick_zero_line, ignoreBounds=True)
    brick_v_line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen((200, 200, 200), width=1))
    brick_plot.addItem(brick_v_line, ignoreBounds=True)
    brick_v_line.hide()

    brick_delta_label = QtWidgets.QLabel(brick_plot)
    brick_delta_label.setStyleSheet("color: white; background: transparent; border: none; padding: 0px; margin: 0px;")
    brick_delta_label.setTextFormat(QtCore.Qt.RichText)
    brick_delta_label.move(40, 2)
    brick_delta_label.hide()

    return {
        "brickDeltaItem": brick_delta_item,
        "brickZeroLine": brick_zero_line,
        "brickVLine": brick_v_line,
        "brickDeltaLabel": brick_delta_label,
    }


def create_kdj_items(kdj_plot):
    kdj_k_curve = kdj_plot.plot(
        pen=pg.mkPen((255, 255, 255), width=1.5),
        name="K",
    )
    kdj_d_curve = kdj_plot.plot(
        pen=pg.mkPen((255, 215, 0), width=1.5),
        name="D",
    )
    kdj_j_curve = kdj_plot.plot(
        pen=pg.mkPen((255, 0, 255), width=1.5),
        name="J",
    )

    kdj_low_line = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen((0, 176, 80), width=1, style=QtCore.Qt.DotLine))
    kdj_mid_line = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen((0, 255, 255), width=1, style=QtCore.Qt.DotLine))
    kdj_high_line = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen((255, 0, 0), width=1, style=QtCore.Qt.DotLine))
    kdj_plot.addItem(kdj_low_line, ignoreBounds=True)
    kdj_low_line.setPos(20)
    kdj_plot.addItem(kdj_mid_line, ignoreBounds=True)
    kdj_mid_line.setPos(50)
    kdj_plot.addItem(kdj_high_line, ignoreBounds=True)
    kdj_high_line.setPos(80)

    kdj_v_line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen((200, 200, 200), width=1))
    kdj_plot.addItem(kdj_v_line, ignoreBounds=True)
    kdj_v_line.hide()

    kdj_label = QtWidgets.QLabel(kdj_plot)
    kdj_label.setStyleSheet("color: white; background: transparent; border: none; padding: 0px; margin: 0px;")
    kdj_label.setTextFormat(QtCore.Qt.RichText)
    kdj_label.move(40, 2)
    kdj_label.hide()

    return {
        "kdjKCurve": kdj_k_curve,
        "kdjDCurve": kdj_d_curve,
        "kdjJCurve": kdj_j_curve,
        "kdjLowLine": kdj_low_line,
        "kdjMidLine": kdj_mid_line,
        "kdjHighLine": kdj_high_line,
        "kdjVLine": kdj_v_line,
        "kdjLabel": kdj_label,
    }
