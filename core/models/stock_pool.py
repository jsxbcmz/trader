from __future__ import annotations

from dataclasses import dataclass, field

from .market import StockInfo


@dataclass(frozen=True, slots=True)
class StockPool:
    name: str
    symbols: tuple[str, ...]
    stocks: tuple[StockInfo, ...] = field(default_factory=tuple)
    source: str = "manual"
