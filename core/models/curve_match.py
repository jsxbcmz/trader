from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class CurveMatchRequest:
    template: np.ndarray
    template_source: str = ""
    top_k: int = 50
    stock_pool_name: str = "default"
    symbols: list[str] | None = None
    enable_multi_scale: bool = False


@dataclass
class CurveMatchItem:
    symbol: str
    name: str
    start_index: int
    start_date: str
    end_date: str
    distance: float
    similarity: float


@dataclass
class CurveMatchResult:
    request: CurveMatchRequest
    matches: list[CurveMatchItem] = field(default_factory=list)
    total_scanned: int = 0
    scan_time_seconds: float = 0.0
