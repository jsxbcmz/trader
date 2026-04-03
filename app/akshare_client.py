from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

try:
    import akshare as ak
except ImportError:  # pragma: no cover
    ak = None


class AkshareClientError(RuntimeError):
    pass


@dataclass
class AkshareClient:

    def _ensure_akshare(self):
        if ak is None:
            raise AkshareClientError("当前环境未安装 akshare，请先执行 pip install akshare")

    def fetch_daily(
        self,
        symbol: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        """获取日线数据。

        Parameters
        ----------
        symbol : str
            6 位股票代码，如 "600519"
        start_date : str, optional
            起始日期，格式 "YYYYMMDD"
        end_date : str, optional
            结束日期，格式 "YYYYMMDD"

        Returns
        -------
        pd.DataFrame
            包含 date, open, close, high, low, volume 列
        """
        self._ensure_akshare()
        try:
            kwargs: dict = {
                "symbol": symbol,
                "period": "daily",
                "adjust": "qfq",
            }
            if start_date:
                kwargs["start_date"] = start_date
            if end_date:
                kwargs["end_date"] = end_date

            df = ak.stock_zh_a_hist(**kwargs)
        except Exception as exc:
            raise AkshareClientError(f"拉取 {symbol} 日线数据失败: {exc}") from exc

        if df is None or df.empty:
            return pd.DataFrame()
        return df.copy()

    def fetch_minute(
        self,
        symbol: str,
        period: str = "5",
    ) -> pd.DataFrame:
        """获取分钟级数据。

        Parameters
        ----------
        symbol : str
            6 位股票代码，如 "600519"
        period : str
            K 线周期，可选 "1", "5", "15", "30", "60"

        Returns
        -------
        pd.DataFrame
            包含分钟级 OHLCV 数据
        """
        self._ensure_akshare()
        try:
            df = ak.stock_zh_a_hist_min_em(symbol=symbol, period=period)
        except Exception as exc:
            raise AkshareClientError(f"拉取 {symbol} {period}分钟数据失败: {exc}") from exc

        if df is None or df.empty:
            return pd.DataFrame()
        return df.copy()

    def fetch_stock_list(self) -> pd.DataFrame:
        """获取 A 股股票列表。

        Returns
        -------
        pd.DataFrame
            包含 symbol, name 等字段
        """
        self._ensure_akshare()
        try:
            df = ak.stock_zh_a_spot_em()
        except Exception as exc:
            raise AkshareClientError(f"获取股票列表失败: {exc}") from exc

        if df is None or df.empty:
            return pd.DataFrame()
        return df.copy()
