from __future__ import annotations


def clean_string(value: str | None, default: str = "") -> str:
    """清理字符串：处理 None、去除首尾空格。"""
    return str(value or default).strip()
