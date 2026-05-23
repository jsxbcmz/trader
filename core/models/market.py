from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StockInfo:
    symbol: str
    name: str = ""
    ts_code: str = ""
    area: str = ""
    industry: str = ""
    market: str = ""


@dataclass(frozen=True)
class DailyDataSpec:
    symbol: str
    file_path: Path
