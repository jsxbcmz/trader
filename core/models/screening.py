from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ScreeningRequest:
    tdx_source: str  # 通达信选股条件代码
    target_date: str
    symbols: tuple[str, ...] = field(default_factory=tuple)
    stock_pool_name: str = "default"
    include_debug: bool = False
    template_id: str = ""      # 模板 ID，用于缓存键
    template_name: str = ""    # 模板名称，用于缓存记录展示


@dataclass(frozen=True)
class ScreeningError:
    symbol: str = ""
    stage: str = ""
    message: str = ""


@dataclass(frozen=True)
class ScreeningMatch:
    symbol: str
    name: str = ""
    requested_date: str = ""
    actual_date: str = ""
    matched: bool = False
    reason: str = ""
    debug: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScreeningResult:
    request: ScreeningRequest
    matches: tuple[ScreeningMatch, ...] = field(default_factory=tuple)
    errors: tuple[ScreeningError, ...] = field(default_factory=tuple)
    total: int = 0
    matched_count: int = 0
