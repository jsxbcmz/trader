from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ExpressionDefinition:
    kind: str
    payload: dict[str, Any]
