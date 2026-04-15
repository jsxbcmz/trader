from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from core.models.market import StockInfo
from core.models.screening import ScreeningError, ScreeningMatch, ScreeningRequest


@dataclass(frozen=True)
class SingleRunResult:
    symbol: str
    name: str = ""
    requested_date: str = ""
    actual_date: str = ""
    matched: bool = False
    reason: str = ""
    value: Any = None
    debug: dict[str, Any] = field(default_factory=dict)

    def to_match(self) -> ScreeningMatch:
        return ScreeningMatch(
            symbol=self.symbol,
            name=self.name,
            requested_date=self.requested_date,
            actual_date=self.actual_date,
            matched=self.matched,
            reason=self.reason,
            debug=self.debug,
        )


@dataclass(frozen=True)
class EngineRunSummary:
    request: ScreeningRequest
    matches: tuple[ScreeningMatch, ...] = field(default_factory=tuple)
    errors: tuple[ScreeningError, ...] = field(default_factory=tuple)
    total: int = 0
    matched_count: int = 0



def build_debug_payload(raw_value: Any, include_debug: bool) -> dict[str, Any]:
    if not include_debug:
        return {}
    if isinstance(raw_value, np.ndarray):
        return {"value_type": "ndarray", "length": int(len(raw_value))}
    if isinstance(raw_value, dict):
        return {"value_type": "dict", "keys": tuple(raw_value.keys())}
    return {"value": raw_value}



def stock_name_of(stock: StockInfo | None, fallback_symbol: str) -> str:
    if stock is None:
        return fallback_symbol
    return stock.name or stock.symbol or fallback_symbol
