from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.utils import clean_string


@dataclass(frozen=True)
class ScreeningTemplate:
    id: str
    name: str
    description: str = ""
    tdx_source: str = ""  # 通达信选股条件代码
    stock_pool_name: str = "default"
    include_debug: bool = False
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "tdx_source": self.tdx_source,
            "stock_pool_name": self.stock_pool_name,
            "include_debug": self.include_debug,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ScreeningTemplate":
        return cls(
            id=clean_string(payload.get("id")),
            name=clean_string(payload.get("name")),
            description=clean_string(payload.get("description")),
            tdx_source=clean_string(payload.get("tdx_source")),
            stock_pool_name=clean_string(payload.get("stock_pool_name"), "default") or "default",
            include_debug=bool(payload.get("include_debug", False)),
            created_at=clean_string(payload.get("created_at")),
            updated_at=clean_string(payload.get("updated_at")),
        )
