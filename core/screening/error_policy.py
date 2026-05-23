from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ErrorPolicy = Literal["skip_symbol", "raise"]

DEFAULT_ERROR_POLICY: ErrorPolicy = "skip_symbol"


@dataclass(frozen=True)
class ErrorContext:
    symbol: str = ""
    stage: str = ""
    message: str = ""



def normalize_error_policy(value: str | None) -> ErrorPolicy:
    normalized = str(value or DEFAULT_ERROR_POLICY).strip().lower()
    if normalized not in {"skip_symbol", "raise"}:
        raise ValueError(f"不支持的错误策略: {value}")
    return normalized  # type: ignore[return-value]
