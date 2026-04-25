from __future__ import annotations

import numpy as np


HTML_Y_VALUE = "<div style='background-color: rgba(20, 20, 20, 0.92); border: 1px solid #d9d9d9; border-radius: 3px; padding: 2px 6px; color: white;'>{text}</div>"


def format_numeric(value: float):
    if np.isnan(value):
        return "--"
    return f"{value:.2f}"


def build_indicator_label_html(short_trend: float, long_short: float):
    return (
        f"<span style='color: #ffffff;'>知行短期趋势线: {format_numeric(float(short_trend))}</span>"
        "&nbsp;&nbsp;"
        f"<span style='color: #ffd700;'>知行多空线: {format_numeric(float(long_short))}</span>"
    )


def build_brick_delta_label_html(diff: float):
    if not np.isfinite(diff):
        diff_text = "--"
        color = "#d9d9d9"
    elif diff > 0:
        diff_text = f"+{diff:.2f}"
        color = "#ff4d4f"
    elif diff < 0:
        diff_text = f"{diff:.2f}"
        color = "#00b050"
    else:
        diff_text = "0.00"
        color = "#d9d9d9"

    return f"<span style='color: #ffffff;'>砖型差值:</span> <span style='color: {color};'>{diff_text}</span>"


def build_kdj_label_html(k: float, d: float, j: float):
    return (
        f"<span style='color: #ffffff;'>K: {format_numeric(float(k))}</span>"
        "&nbsp;&nbsp;"
        f"<span style='color: #ffd700;'>D: {format_numeric(float(d))}</span>"
        "&nbsp;&nbsp;"
        f"<span style='color: #ff00ff;'>J: {format_numeric(float(j))}</span>"
    )


def build_needle20_label_html(short: float, mid: float, long: float):
    return (
        f"<span style='color: #ffffff;'>短期: {format_numeric(float(short))}</span>"
        "&nbsp;&nbsp;"
        f"<span style='color: #ffd700;'>中期: {format_numeric(float(mid))}</span>"
        "&nbsp;&nbsp;"
        f"<span style='color: #ff00ff;'>长期: {format_numeric(float(long))}</span>"
    )


def build_y_value_html(text: str):
    return HTML_Y_VALUE.format(text=text)


def format_tooltip_value(value, nd=2):
    if value != value:
        return "--"
    return f"{value:.{nd}f}"


def build_info_box_html(ds: str, close_value: float, pct: float, amount_yi: float, turnover_rate: float = float("nan"),
                        open_value: float = float("nan"), high_value: float = float("nan"), low_value: float = float("nan")):
    if pct == pct:
        if pct > 0:
            accent_color = "#ff4d4f"
            border_color = "#ff7875"
            bg_color = "rgba(80, 0, 0, 0.92)"
        elif pct < 0:
            accent_color = "#00b050"
            border_color = "#34c759"
            bg_color = "rgba(0, 50, 0, 0.92)"
        else:
            accent_color = "#d9d9d9"
            border_color = "#8c8c8c"
            bg_color = "rgba(30, 30, 30, 0.92)"
    else:
        accent_color = "#d9d9d9"
        border_color = "#8c8c8c"
        bg_color = "rgba(30, 30, 30, 0.92)"

    return (
        f"<div style='background-color: {bg_color}; border: 1px solid {border_color}; "
        f"border-radius: 4px; padding: 6px 8px;'>"
        f"<div style='color: white;'>{ds}</div>"
        f"<div style='color: white;'>开盘价 {format_tooltip_value(open_value)}</div>"
        f"<div style='color: white;'>收盘价 {format_tooltip_value(close_value)}</div>"
        f"<div style='color: white;'>最高价 {format_tooltip_value(high_value)}</div>"
        f"<div style='color: white;'>最低价 {format_tooltip_value(low_value)}</div>"
        f"<div style='color: {accent_color};'>涨跌幅 {format_tooltip_value(pct, 2)}%</div>"
        f"<div style='color: white;'>成交额 {format_tooltip_value(amount_yi, 2)} 亿</div>"
        f"<div style='color: white;'>换手率 {format_tooltip_value(turnover_rate, 2)}%</div>"
        f"</div>"
    )
