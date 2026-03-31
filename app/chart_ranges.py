from __future__ import annotations

import numpy as np


def clamp_xrange(x0: float, x1: float, x_min_allowed: float, x_max_allowed: float, min_width: float, max_width: float):
    if x1 <= x0:
        return None

    width = x1 - x0
    epsilon = 1e-6
    new_x0, new_x1 = x0, x1

    if width > max_width + epsilon:
        center = (x0 + x1) / 2.0
        new_x0 = center - max_width / 2.0
        new_x1 = center + max_width / 2.0
    elif width < min_width - epsilon:
        center = (x0 + x1) / 2.0
        new_x0 = center - min_width / 2.0
        new_x1 = center + min_width / 2.0

    width = new_x1 - new_x0

    if new_x0 < x_min_allowed:
        new_x0 = x_min_allowed
        new_x1 = new_x0 + width
    if new_x1 > x_max_allowed:
        new_x1 = x_max_allowed
        new_x0 = new_x1 - width

    return new_x0, new_x1


def visible_index_range(x0: float, x1: float, item_half_width: float, length: int):
    left = max(0, int(np.floor(x0 + item_half_width)))
    right = min(length - 1, int(np.ceil(x1 - item_half_width)))
    if right < left:
        left = max(0, int(np.floor(x0)))
        right = min(length - 1, int(np.ceil(x1)))
        if right < left:
            return None
    return left, right


def padded_min_max(low_values: np.ndarray, high_values: np.ndarray):
    if len(low_values) == 0 or len(high_values) == 0:
        return None

    ymin = float(np.nanmin(low_values))
    ymax = float(np.nanmax(high_values))
    if not np.isfinite(ymin) or not np.isfinite(ymax):
        return None

    if ymax <= ymin:
        pad = max(abs(ymin) * 0.04, 0.01)
    else:
        pad = (ymax - ymin) * 0.04

    return ymin - pad, ymax + pad
