from __future__ import annotations

import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets


def window_width_for_days(days: int, item_half_width: float, right_view_padding: float, include_right_padding: bool):
    base_width = max(0, days - 1) + item_half_width * 2
    if include_right_padding:
        base_width += right_view_padding
    return base_width


class StockChartViewBox(pg.ViewBox):
    """ViewBox that blocks wheel zoom once x-range reaches configured bounds."""

    def __init__(self, owner):
        super().__init__()
        self._owner = owner
        self._is_price_panel = False

    def wheelEvent(self, ev, axis=None):
        owner = self._owner
        if owner is None or owner._df is None or len(owner._df) == 0:
            super().wheelEvent(ev, axis=axis)
            return

        (x0, x1), _ = self.viewRange()
        width = x1 - x0
        full_width = float(owner._x_max) - float(owner._x_min)
        if width <= 0 or full_width <= 0:
            super().wheelEvent(ev, axis=axis)
            return

        min_width = min(owner._window_width_for_days(owner._min_visible_days, include_right_padding=False), full_width)
        max_width = min(owner._window_width_for_days(owner._max_visible_days, include_right_padding=True), full_width)
        if max_width < min_width:
            max_width = min_width

        delta = ev.delta() if hasattr(ev, "delta") else ev.angleDelta().y()
        epsilon = 1e-6

        if (delta > 0 and width <= min_width + epsilon) or (delta < 0 and width >= max_width - epsilon):
            ev.accept()
            return

        super().wheelEvent(ev, axis=axis)

    def raiseContextMenu(self, ev):
        if self._is_price_panel:
            ev.accept()
            return

        owner = self._owner
        if owner is None:
            ev.accept()
            return

        from .chart_layout import SUB_CHART_META, SubChartType

        menu = QtWidgets.QMenu()
        menu.setStyleSheet(
            "QMenu { padding: 4px; }"
            "QCheckBox { padding: 2px 8px; spacing: 6px; }"
        )

        current = list(owner._visible_sub_charts)
        all_types = list(SubChartType)

        checkboxes: dict[SubChartType, QtWidgets.QCheckBox] = {}
        for chart_type in all_types:
            meta = SUB_CHART_META[chart_type]
            cb = QtWidgets.QCheckBox(meta["label"])
            cb.setChecked(chart_type in current)
            wa = QtWidgets.QWidgetAction(menu)
            wa.setDefaultWidget(cb)
            menu.addAction(wa)
            checkboxes[chart_type] = cb

        def on_indicator_toggled():
            selected = [t for t, cb in checkboxes.items() if cb.isChecked()]
            if not selected:
                sender = menu.sender()
                if isinstance(sender, QtWidgets.QCheckBox):
                    sender.blockSignals(True)
                    sender.setChecked(True)
                    sender.blockSignals(False)
                return
            owner._apply_sub_chart_selection(selected)

        for cb in checkboxes.values():
            cb.stateChanged.connect(on_indicator_toggled)

        pos = ev.screenPos() if hasattr(ev, 'screenPos') else ev.globalPos()
        menu.exec(QtCore.QPoint(int(pos.x()), int(pos.y())))
        ev.accept()
