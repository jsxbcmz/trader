"""统计页常量与拼音首字母工具。"""
from __future__ import annotations

# ── 接口名称到内部 ID 的映射 ──────────────────────────────────────────────
API_ID_MAP = {
    "比赛排名": "api1",
    "每日持仓": "api2",
}

API_DISPLAY_NAMES = {
    "api1": "📋 比赛排名",
    "api2": "📈 每日持仓",
}

# ── 持仓操作映射 ──────────────────────────────────────────────────────────
OPERATION_MAP = {
    "0": {"label": "不变", "color": "#94a3b8", "background": "#334155"},
    "1": {"label": "加仓", "color": "#6ee7b7", "background": "#1a332e"},
    "2": {"label": "减仓", "color": "#fbbf24", "background": "#3b2c1c"},
    "3": {"label": "建仓", "color": "#4ade80", "background": "#1a3329"},
    "4": {"label": "清仓", "color": "#f87171", "background": "#3b1c1c"},
    "7": {"label": "大幅加仓", "color": "#22c55e", "background": "#14532d"},
    "8": {"label": "大幅减仓", "color": "#f97316", "background": "#451a03"},
    "9": {"label": "T操作", "color": "#60a5fa", "background": "#1e3a5f"},
}

OPERATION_SORT_ORDER = {"3": 1, "7": 2, "1": 3, "9": 4, "0": 5, "2": 6, "8": 7, "4": 8}


def _char_initial(char: str) -> str:
    """获取单个汉字的拼音首字母（基于 GB2312 编码区间）"""
    if char.isascii() and char.isalpha():
        return char.lower()
    if not ("\u4e00" <= char <= "\u9fff"):
        return ""
    try:
        gb_bytes = char.encode("gb2312")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return ""
    if len(gb_bytes) != 2:
        return ""
    code = gb_bytes[0] * 256 + gb_bytes[1]
    if code < 0xB0A1:
        return ""
    # GB2312 一级汉字按拼音排序的区间边界
    bounds = (
        (0xB0A1, "a"), (0xB0C5, "b"), (0xB2C1, "c"), (0xB4EE, "d"),
        (0xB6EA, "e"), (0xB7A2, "f"), (0xB8C1, "g"), (0xB9FE, "h"),
        (0xBBF7, "j"), (0xBFA6, "k"), (0xC0AC, "l"), (0xC2E8, "m"),
        (0xC4C3, "n"), (0xC5B6, "o"), (0xC5BE, "p"), (0xC6DA, "q"),
        (0xC8BB, "r"), (0xC8F6, "s"), (0xCBFA, "t"), (0xCDDA, "w"),
        (0xCEF4, "x"), (0xD1B9, "y"), (0xD4D1, "z"),
    )
    for boundary, letter in reversed(bounds):
        if code >= boundary:
            return letter
    return ""


def _name_initials(text: str) -> str:
    """获取中文名称的拼音首字母（小写）"""
    text = str(text or "").strip()
    if not text:
        return ""
    return "".join(_char_initial(ch) for ch in text)
