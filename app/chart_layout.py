from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from .chart_primitives import BrickDeltaItem, CandlestickItem, DateAxisItem
from .chart_interaction import StockChartViewBox


class SubChartType(str, Enum):
    VOLUME = "volume"
    BRICK = "brick"
    KDJ = "kdj"
    NEEDLE20 = "needle20"


SUB_CHART_META = {
    SubChartType.VOLUME:   {"label": "成交额",   "order": 0},
    SubChartType.BRICK:    {"label": "砖型差值", "order": 1},
    SubChartType.KDJ:      {"label": "KDJ",      "order": 2},
    SubChartType.NEEDLE20: {"label": "单针下20", "order": 3},
}

DEFAULT_SUB_CHARTS = [SubChartType.VOLUME, SubChartType.BRICK, SubChartType.KDJ]


@dataclass
class PlotBundle:
    """四联图的 PlotWidget、Axis、ViewBox 集合。"""
    price_axis: DateAxisItem
    vol_axis: DateAxisItem
    brick_axis: DateAxisItem
    kdj_axis: DateAxisItem
    needle20_axis: DateAxisItem
    price_viewbox: StockChartViewBox
    vol_viewbox: StockChartViewBox
    brick_viewbox: StockChartViewBox
    kdj_viewbox: StockChartViewBox
    needle20_viewbox: StockChartViewBox
    price_plot: pg.PlotWidget
    vol_plot: pg.PlotWidget
    brick_plot: pg.PlotWidget
    kdj_plot: pg.PlotWidget
    needle20_plot: pg.PlotWidget


@dataclass
class PriceItems:
    """K 线面板的图形项集合。"""
    candle_item: CandlestickItem
    zx_short_trend: pg.PlotDataItem
    zx_long_short: pg.PlotDataItem
    v_line: pg.InfiniteLine
    h_line: pg.InfiniteLine
    info_text: pg.TextItem
    y_value_text: pg.TextItem
    indicator_label: QtWidgets.QLabel
    stock_info_label: QtWidgets.QLabel
    price_guide_lines: list[pg.InfiniteLine]
    price_guide_labels: list[pg.TextItem]


@dataclass
class VolumeItems:
    """成交额面板的图形项集合。"""
    vol_v_line: pg.InfiniteLine


@dataclass
class BrickItems:
    """砖型差值面板的图形项集合。"""
    brick_delta_item: BrickDeltaItem
    brick_zero_line: pg.InfiniteLine
    brick_v_line: pg.InfiniteLine
    brick_delta_label: QtWidgets.QLabel


@dataclass
class KdjItems:
    """KDJ 面板的图形项集合。"""
    kdj_k_curve: pg.PlotDataItem
    kdj_d_curve: pg.PlotDataItem
    kdj_j_curve: pg.PlotDataItem
    kdj_low_line: pg.InfiniteLine
    kdj_mid_line: pg.InfiniteLine
    kdj_high_line: pg.InfiniteLine
    kdj_v_line: pg.InfiniteLine
    kdj_label: QtWidgets.QLabel


@dataclass
class Needle20Items:
    """单针下20面板的图形项集合。"""
    short_curve: pg.PlotDataItem
    mid_curve: pg.PlotDataItem
    long_curve: pg.PlotDataItem
    low_line: pg.InfiniteLine
    high_line: pg.InfiniteLine
    v_line: pg.InfiniteLine
    label: QtWidgets.QLabel


@dataclass
class SubChartSeparators:
    """副图与主图之间的分隔线。"""
    vol_separator: QtWidgets.QWidget
    brick_separator: QtWidgets.QWidget
    kdj_separator: QtWidgets.QWidget
    needle20_separator: QtWidgets.QWidget


@dataclass
class DateBarItems:
    """底部时间标注栏的组件集合。"""
    date_bar: QtWidgets.QWidget
    left_date_label: QtWidgets.QLabel
    right_date_label: QtWidgets.QLabel
    crosshair_date_label: QtWidgets.QLabel


FIXED_Y_AXIS_WIDTH = 50


def configure_plot_widget(plot: pg.PlotWidget):
    plot.setViewportUpdateMode(QtWidgets.QGraphicsView.FullViewportUpdate)
    plot.showGrid(x=True, y=False, alpha=0.25)
    plot.setMouseEnabled(x=True, y=False)


def create_plot_bundle(owner) -> PlotBundle:
    price_axis = DateAxisItem([], orientation="bottom")
    vol_axis = DateAxisItem([], orientation="bottom")
    brick_axis = DateAxisItem([], orientation="bottom")
    kdj_axis = DateAxisItem([], orientation="bottom")
    needle20_axis = DateAxisItem([], orientation="bottom")

    price_viewbox = StockChartViewBox(owner)
    vol_viewbox = StockChartViewBox(owner)
    brick_viewbox = StockChartViewBox(owner)
    kdj_viewbox = StockChartViewBox(owner)
    needle20_viewbox = StockChartViewBox(owner)

    price_plot = pg.PlotWidget(axisItems={"bottom": price_axis}, viewBox=price_viewbox)
    vol_plot = pg.PlotWidget(axisItems={"bottom": vol_axis}, viewBox=vol_viewbox)
    brick_plot = pg.PlotWidget(axisItems={"bottom": brick_axis}, viewBox=brick_viewbox)
    kdj_plot = pg.PlotWidget(axisItems={"bottom": kdj_axis}, viewBox=kdj_viewbox)
    needle20_plot = pg.PlotWidget(axisItems={"bottom": needle20_axis}, viewBox=needle20_viewbox)

    for plot in (price_plot, vol_plot, brick_plot, kdj_plot, needle20_plot):
        configure_plot_widget(plot)
        plot_item = plot.getPlotItem()
        plot_item.hideButtons()
        plot.getAxis("bottom").setStyle(showValues=False)
        plot.getAxis("bottom").setHeight(0)
        left_axis = plot.getAxis("left")
        left_axis.setStyle(showValues=False)
        left_axis.setTicks([])
        left_axis.setWidth(0)

    price_plot.setXLink(vol_plot)
    brick_plot.setXLink(price_plot)
    kdj_plot.setXLink(price_plot)
    needle20_plot.setXLink(price_plot)

    return PlotBundle(
        price_axis=price_axis,
        vol_axis=vol_axis,
        brick_axis=brick_axis,
        kdj_axis=kdj_axis,
        needle20_axis=needle20_axis,
        price_viewbox=price_viewbox,
        vol_viewbox=vol_viewbox,
        brick_viewbox=brick_viewbox,
        kdj_viewbox=kdj_viewbox,
        needle20_viewbox=needle20_viewbox,
        price_plot=price_plot,
        vol_plot=vol_plot,
        brick_plot=brick_plot,
        kdj_plot=kdj_plot,
        needle20_plot=needle20_plot,
    )

def _create_separator() -> QtWidgets.QWidget:
    sep = QtWidgets.QWidget()
    sep.setFixedHeight(1)
    sep.setStyleSheet("background-color: #4a4a4a;")
    return sep


def create_chart_layout(owner, price_plot, vol_plot, brick_plot, kdj_plot, needle20_plot):
    chart_container = QtWidgets.QWidget()
    chart_layout = QtWidgets.QVBoxLayout(chart_container)
    chart_layout.setContentsMargins(0, 0, 16, 0)
    chart_layout.setSpacing(0)

    chart_layout.addWidget(price_plot, 3)

    vol_sep = _create_separator()
    chart_layout.addWidget(vol_sep, 0)
    chart_layout.addWidget(vol_plot, 1)

    brick_sep = _create_separator()
    chart_layout.addWidget(brick_sep, 0)
    chart_layout.addWidget(brick_plot, 1)

    kdj_sep = _create_separator()
    chart_layout.addWidget(kdj_sep, 0)
    chart_layout.addWidget(kdj_plot, 1)

    needle20_sep = _create_separator()
    chart_layout.addWidget(needle20_sep, 0)
    chart_layout.addWidget(needle20_plot, 1)

    date_bar_items = _create_date_bar()
    chart_layout.addWidget(date_bar_items.date_bar, 0)

    separators = SubChartSeparators(
        vol_separator=vol_sep,
        brick_separator=brick_sep,
        kdj_separator=kdj_sep,
        needle20_separator=needle20_sep,
    )

    layout = QtWidgets.QVBoxLayout(owner)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(chart_container)

    return chart_container, chart_layout, layout, date_bar_items, separators


def _create_date_bar() -> DateBarItems:
    """创建底部时间标注栏。"""
    date_bar = QtWidgets.QWidget()
    date_bar_layout = QtWidgets.QHBoxLayout(date_bar)
    date_bar_layout.setContentsMargins(FIXED_Y_AXIS_WIDTH, 2, 0, 2)
    date_bar_layout.setSpacing(0)

    label_style = "color: #999; font-size: 11px; background: transparent;"

    left_date_label = QtWidgets.QLabel("")
    left_date_label.setStyleSheet(label_style)

    right_date_label = QtWidgets.QLabel("")
    right_date_label.setStyleSheet(label_style)
    right_date_label.setAlignment(QtCore.Qt.AlignRight)

    date_bar_layout.addWidget(left_date_label)
    date_bar_layout.addStretch(1)
    date_bar_layout.addWidget(right_date_label)

    date_bar.setFixedHeight(22)

    crosshair_date_label = QtWidgets.QLabel(date_bar)
    crosshair_date_label.setStyleSheet(
        "color: white; background: rgba(60, 60, 60, 0.9); "
        "border-radius: 3px; padding: 1px 6px; font-size: 11px;"
    )
    crosshair_date_label.hide()

    return DateBarItems(
        date_bar=date_bar,
        left_date_label=left_date_label,
        right_date_label=right_date_label,
        crosshair_date_label=crosshair_date_label,
    )


def create_price_items(price_plot) -> PriceItems:
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

    stock_info_label = QtWidgets.QLabel(price_plot)
    stock_info_label.setStyleSheet(
        "color: rgba(255, 255, 255, 0.5); background: transparent; "
        "border: none; padding: 0px; margin: 0px; font-size: 13px; font-weight: bold;"
    )
    stock_info_label.move(8, 4)
    stock_info_label.hide()

    indicator_label = QtWidgets.QLabel(price_plot)
    indicator_label.setStyleSheet("color: white; background: transparent; border: none; padding: 0px; margin: 0px;")
    indicator_label.setTextFormat(QtCore.Qt.RichText)
    indicator_label.move(8, 24)
    indicator_label.hide()

    num_price_guides = 4
    price_guide_lines: list[pg.InfiniteLine] = []
    price_guide_labels: list[pg.TextItem] = []
    guide_pen = pg.mkPen((255, 255, 255, 40), width=1, style=QtCore.Qt.DashLine)
    for _ in range(num_price_guides):
        guide_line = pg.InfiniteLine(angle=0, movable=False, pen=guide_pen)
        guide_line.setZValue(-10)
        price_plot.addItem(guide_line, ignoreBounds=True)
        guide_line.hide()
        price_guide_lines.append(guide_line)

        guide_label = pg.TextItem(anchor=(0, 0.5), color=(180, 180, 180, 160))
        guide_label.setZValue(-10)
        price_plot.addItem(guide_label, ignoreBounds=True)
        guide_label.hide()
        price_guide_labels.append(guide_label)

    return PriceItems(
        candle_item=candle_item,
        zx_short_trend=zx_short_trend,
        zx_long_short=zx_long_short,
        v_line=v_line,
        h_line=h_line,
        info_text=info_text,
        y_value_text=y_value_text,
        indicator_label=indicator_label,
        stock_info_label=stock_info_label,
        price_guide_lines=price_guide_lines,
        price_guide_labels=price_guide_labels,
    )


def create_volume_items(vol_plot) -> VolumeItems:
    vol_v_line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen((200, 200, 200), width=1))
    vol_plot.addItem(vol_v_line, ignoreBounds=True)
    vol_v_line.hide()
    return VolumeItems(vol_v_line=vol_v_line)


def create_brick_items(brick_plot) -> BrickItems:
    brick_delta_item = BrickDeltaItem()
    brick_plot.addItem(brick_delta_item)

    brick_zero_line = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen((120, 120, 120, 0), width=0))
    brick_plot.addItem(brick_zero_line, ignoreBounds=True)
    brick_v_line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen((200, 200, 200), width=1))
    brick_plot.addItem(brick_v_line, ignoreBounds=True)
    brick_v_line.hide()

    brick_delta_label = QtWidgets.QLabel(brick_plot)
    brick_delta_label.setStyleSheet("color: white; background: transparent; border: none; padding: 0px; margin: 0px;")
    brick_delta_label.setTextFormat(QtCore.Qt.RichText)
    brick_delta_label.move(40, 2)
    brick_delta_label.hide()

    return BrickItems(
        brick_delta_item=brick_delta_item,
        brick_zero_line=brick_zero_line,
        brick_v_line=brick_v_line,
        brick_delta_label=brick_delta_label,
    )


def create_kdj_items(kdj_plot) -> KdjItems:
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

    return KdjItems(
        kdj_k_curve=kdj_k_curve,
        kdj_d_curve=kdj_d_curve,
        kdj_j_curve=kdj_j_curve,
        kdj_low_line=kdj_low_line,
        kdj_mid_line=kdj_mid_line,
        kdj_high_line=kdj_high_line,
        kdj_v_line=kdj_v_line,
        kdj_label=kdj_label,
    )


def create_needle20_items(needle20_plot) -> Needle20Items:
    short_curve = needle20_plot.plot(pen=pg.mkPen((255, 255, 255), width=1.5), name="短期")
    mid_curve = needle20_plot.plot(pen=pg.mkPen((255, 215, 0), width=1.5), name="中期")
    long_curve = needle20_plot.plot(pen=pg.mkPen((255, 0, 255), width=1.5), name="长期")

    low_line = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen((0, 176, 80), width=1, style=QtCore.Qt.DotLine))
    needle20_plot.addItem(low_line, ignoreBounds=True)
    low_line.setPos(20)

    high_line = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen((255, 0, 0), width=1, style=QtCore.Qt.DotLine))
    needle20_plot.addItem(high_line, ignoreBounds=True)
    high_line.setPos(80)

    v_line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen((200, 200, 200), width=1))
    needle20_plot.addItem(v_line, ignoreBounds=True)
    v_line.hide()

    label = QtWidgets.QLabel(needle20_plot)
    label.setStyleSheet("color: white; background: transparent; border: none; padding: 0px; margin: 0px;")
    label.setTextFormat(QtCore.Qt.RichText)
    label.move(40, 2)
    label.hide()

    return Needle20Items(
        short_curve=short_curve,
        mid_curve=mid_curve,
        long_curve=long_curve,
        low_line=low_line,
        high_line=high_line,
        v_line=v_line,
        label=label,
    )


class SubChartSelector(QtWidgets.QToolButton):
    """副图指标多选下拉框。"""

    selectionChanged = QtCore.Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setText("副图指标 ▼")
        self.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.setStyleSheet(
            "QToolButton { padding: 4px 8px; }"
            "QToolButton::menu-indicator { image: none; }"
        )

        self._menu = QtWidgets.QMenu(self)
        self._menu.setStyleSheet(
            "QMenu { padding: 4px; }"
            "QCheckBox { padding: 4px 8px; spacing: 6px; }"
        )
        self._checkboxes: dict[SubChartType, QtWidgets.QCheckBox] = {}

        for chart_type in SubChartType:
            meta = SUB_CHART_META[chart_type]
            checkbox = QtWidgets.QCheckBox(meta["label"])
            checkbox.setChecked(chart_type in DEFAULT_SUB_CHARTS)
            checkbox.stateChanged.connect(self._on_checkbox_changed)

            action = QtWidgets.QWidgetAction(self._menu)
            action.setDefaultWidget(checkbox)
            self._menu.addAction(action)
            self._checkboxes[chart_type] = checkbox

        self.setMenu(self._menu)

    def _on_checkbox_changed(self):
        selected = [t for t, cb in self._checkboxes.items() if cb.isChecked()]
        if not selected:
            sender = self.sender()
            if isinstance(sender, QtWidgets.QCheckBox):
                sender.blockSignals(True)
                sender.setChecked(True)
                sender.blockSignals(False)
            selected = [t for t, cb in self._checkboxes.items() if cb.isChecked()]

        for t, cb in self._checkboxes.items():
            if len(selected) <= 1 and cb.isChecked():
                cb.setEnabled(False)
            else:
                cb.setEnabled(True)

        self.selectionChanged.emit(selected)

    def get_selected(self) -> list[SubChartType]:
        return [t for t, cb in self._checkboxes.items() if cb.isChecked()]

    def set_selected(self, types: list[SubChartType]):
        for t, cb in self._checkboxes.items():
            cb.blockSignals(True)
            cb.setChecked(t in types)
            cb.blockSignals(False)
        selected = [t for t, cb in self._checkboxes.items() if cb.isChecked()]
        for t, cb in self._checkboxes.items():
            if len(selected) <= 1 and cb.isChecked():
                cb.setEnabled(False)
            else:
                cb.setEnabled(True)