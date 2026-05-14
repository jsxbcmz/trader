from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from core.data.io import get_daily_csv_path, load_daily_csv, load_stock_list, normalize_daily_dataframe
from core.models.market import StockInfo


@dataclass
class StockRepository:
    root: Path

    @property
    def stocklist_csv(self) -> Path:
        return self.root / "stocklist.csv"

    @property
    def stock_daily_data_dir(self) -> Path:
        return self.root / "stock_daily_data"

    def get_stock_list_frame(self) -> pd.DataFrame:
        return load_stock_list(self.stocklist_csv).copy()

    def get_stock_infos(self) -> list[StockInfo]:
        df = self.get_stock_list_frame()
        stocks: list[StockInfo] = []
        for _, row in df.iterrows():
            stocks.append(
                StockInfo(
                    symbol=str(row.get("symbol", "") or "").zfill(6),
                    name=str(row.get("name", "") or ""),
                    ts_code=str(row.get("ts_code", "") or ""),
                    area=str(row.get("area", "") or ""),
                    industry=str(row.get("industry", "") or ""),
                    market=str(row.get("market", "") or ""),
                )
            )
        return stocks

    def get_daily_frame(self, symbol: str) -> pd.DataFrame:
        return load_daily_csv(self.stock_daily_data_dir, symbol).copy()

    def normalize_daily_frame(self, df: pd.DataFrame) -> pd.DataFrame:
        return normalize_daily_dataframe(df)

    def get_daily_path(self, symbol: str) -> Path:
        return get_daily_csv_path(self.stock_daily_data_dir, symbol)
