from __future__ import annotations

from pathlib import Path

from core.data.repository import StockRepository
from core.stock_pool.manager import StockPoolManager



def test_stock_pool_manager_default_pool(temp_root: Path):
    manager = StockPoolManager(StockRepository(temp_root))
    pool = manager.get_default_pool()
    assert pool.name == "default"
    assert pool.symbols == ("000001", "000002")
    assert len(pool.stocks) == 2



def test_stock_pool_manager_symbol_pool_deduplicates(temp_root: Path):
    manager = StockPoolManager(StockRepository(temp_root))
    pool = manager.get_pool_by_symbols(["1", "000001", "000002", "000002"])
    assert pool.symbols == ("000001", "000002")
    assert len(pool.stocks) == 2
