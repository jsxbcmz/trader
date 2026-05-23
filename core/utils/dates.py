from __future__ import annotations

from datetime import datetime


def now_iso() -> str:
    """获取当前时间的 ISO 格式字符串（秒级精度）。"""
    return datetime.now().isoformat(timespec="seconds")
