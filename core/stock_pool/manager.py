from __future__ import annotations

from dataclasses import dataclass

from core.data.repository import StockRepository
from core.models.market import StockInfo
from core.models.stock_pool import StockPool


@dataclass
class StockPoolManager:
    repository: StockRepository

    def get_default_pool(self, name: str = "default") -> StockPool:
        stocks = tuple(self.repository.get_stock_infos())
        symbols = tuple(stock.symbol for stock in stocks)
        return StockPool(name=name, symbols=symbols, stocks=stocks, source="stocklist")

    def get_pool_by_symbols(self, symbols: list[str] | tuple[str, ...], name: str = "custom") -> StockPool:
        normalized_symbols = tuple(self._normalize_symbols(symbols))
        stock_map = {stock.symbol: stock for stock in self.repository.get_stock_infos()}
        stocks = tuple(stock_map[symbol] for symbol in normalized_symbols if symbol in stock_map)
        return StockPool(name=name, symbols=normalized_symbols, stocks=stocks, source="symbols")

    def _normalize_symbols(self, symbols: list[str] | tuple[str, ...]):
        seen: set[str] = set()
        for symbol in symbols:
            normalized = str(symbol or "").strip().zfill(6)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            yield normalized
