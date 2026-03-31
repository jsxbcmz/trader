from __future__ import annotations

import pyqtgraph as pg


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
