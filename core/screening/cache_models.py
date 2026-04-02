from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

CACHE_STATUS_COMPLETED = "completed"
CACHE_STATUS_INTERRUPTED = "interrupted"


@dataclass(slots=True)
class ScreeningCacheEntry:
    """单条选股缓存记录（精简版）

    存储策略：
    - processed_count: 仅记录已处理股票数量（而非完整列表），用于断点续选时计算偏移
    - matched_symbols: 仅记录匹配的股票代码列表（而非完整 match 详情）
    - error_count: 仅记录错误数量（而非完整错误详情）
    """

    target_date: str
    template_id: str
    template_name: str
    tdx_source_hash: str
    stock_pool_name: str
    status: str  # "completed" | "interrupted"
    total: int
    processed_count: int = 0
    matched_symbols: list[dict[str, str]] = field(default_factory=list)
    error_count: int = 0
    created_at: str = ""
    updated_at: str = ""

    @property
    def is_completed(self) -> bool:
        return self.status == CACHE_STATUS_COMPLETED

    @property
    def is_interrupted(self) -> bool:
        return self.status == CACHE_STATUS_INTERRUPTED

    @property
    def matched_count(self) -> int:
        return len(self.matched_symbols)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_date": self.target_date,
            "template_id": self.template_id,
            "template_name": self.template_name,
            "tdx_source_hash": self.tdx_source_hash,
            "stock_pool_name": self.stock_pool_name,
            "status": self.status,
            "total": self.total,
            "processed_count": self.processed_count,
            "matched_symbols": self.matched_symbols,  # [{symbol, name}, ...]
            "error_count": self.error_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScreeningCacheEntry:
        # 兼容旧格式：processed_symbols → processed_count
        if "processed_symbols" in data and "processed_count" not in data:
            processed_count = len(data["processed_symbols"])
        else:
            processed_count = int(data.get("processed_count", 0))

        # 兼容旧格式：matches (list[dict]) → matched_symbols (list[{symbol,name}])
        if "matches" in data and "matched_symbols" not in data:
            old_matches = data.get("matches", [])
            matched_symbols = [
                {"symbol": m["symbol"], "name": m.get("name", "")}
                for m in old_matches
                if isinstance(m, dict) and m.get("matched", False)
            ]
        else:
            raw = list(data.get("matched_symbols", []))
            # 兼容纯字符串列表的旧格式
            matched_symbols = [
                ({"symbol": item, "name": ""} if isinstance(item, str) else item)
                for item in raw
            ]

        # 兼容旧格式：errors (list[dict]) → error_count
        if "errors" in data and "error_count" not in data:
            error_count = len(data.get("errors", []))
        else:
            error_count = int(data.get("error_count", 0))

        return cls(
            target_date=str(data.get("target_date", "")),
            template_id=str(data.get("template_id", "")),
            template_name=str(data.get("template_name", "")),
            tdx_source_hash=str(data.get("tdx_source_hash", "")),
            stock_pool_name=str(data.get("stock_pool_name", "default")),
            status=str(data.get("status", "")),
            total=int(data.get("total", 0)),
            processed_count=processed_count,
            matched_symbols=matched_symbols,
            error_count=error_count,
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
        )


def compute_tdx_source_hash(tdx_source: str) -> str:
    """对通达信条件代码计算 SHA-256 哈希，用于缓存键比对"""
    normalized = (tdx_source or "").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
