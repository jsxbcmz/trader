from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ScreeningTemplate:
    id: str
    name: str
    description: str = ""
    condition: dict[str, Any] | None = None
    default_time_mode: str = "exact"
    stock_pool_name: str = "default"
    include_debug: bool = False
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "condition": self.condition or {},
            "default_time_mode": self.default_time_mode,
            "stock_pool_name": self.stock_pool_name,
            "include_debug": self.include_debug,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ScreeningTemplate":
        return cls(
            id=str(payload.get("id", "") or "").strip(),
            name=str(payload.get("name", "") or "").strip(),
            description=str(payload.get("description", "") or "").strip(),
            condition=dict(payload.get("condition") or {}),
            default_time_mode=str(payload.get("default_time_mode", "exact") or "exact").strip() or "exact",
            stock_pool_name=str(payload.get("stock_pool_name", "default") or "default").strip() or "default",
            include_debug=bool(payload.get("include_debug", False)),
            created_at=str(payload.get("created_at", "") or "").strip(),
            updated_at=str(payload.get("updated_at", "") or "").strip(),
        )
