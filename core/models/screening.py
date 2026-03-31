from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ScreeningRequest:
    condition: Any
    target_date: str
    time_mode: str = "exact"
    symbols: tuple[str, ...] = field(default_factory=tuple)
    stock_pool_name: str = "default"
    include_debug: bool = False


@dataclass(frozen=True, slots=True)
class ScreeningError:
    symbol: str = ""
    stage: str = ""
    message: str = ""


@dataclass(frozen=True, slots=True)
class ScreeningMatch:
    symbol: str
    name: str = ""
    requested_date: str = ""
    actual_date: str = ""
    matched: bool = False
    reason: str = ""
    debug: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ScreeningResult:
    request: ScreeningRequest
    matches: tuple[ScreeningMatch, ...] = field(default_factory=tuple)
    errors: tuple[ScreeningError, ...] = field(default_factory=tuple)
    total: int = 0
    matched_count: int = 0
