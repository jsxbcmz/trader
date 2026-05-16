"""主板股票池过滤器（P0-1）。

静态过滤：仅依据 stocklist.csv 的 symbol / name 字段，与日期/行情无关。
当日停牌等动态过滤在 P0-2b 数据加载阶段做。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.data.repository import StockRepository
from core.models.market import StockInfo


NON_MAIN_BOARD_PREFIXES = ("30", "68", "8", "4")


def _is_main_board(symbol: str) -> bool:
    s = (symbol or "").strip()
    if not s:
        return False
    s = s.zfill(6)
    return not any(s.startswith(p) for p in NON_MAIN_BOARD_PREFIXES)


def _is_st(name: str) -> bool:
    return "ST" in (name or "").upper()


@dataclass
class MainBoardPool:
    repository: StockRepository

    @classmethod
    def from_root(cls, root: Path) -> "MainBoardPool":
        return cls(repository=StockRepository(root=root))

    def list_active(self) -> list[StockInfo]:
        """主板候选清单（剔创业板/科创板/北交所/ST）。"""
        return [
            s for s in self.repository.get_stock_infos()
            if _is_main_board(s.symbol) and not _is_st(s.name)
        ]
