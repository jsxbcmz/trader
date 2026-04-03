from __future__ import annotations

import os
from dataclasses import dataclass

import pandas as pd

try:
    import tushare as ts
except ImportError:  # pragma: no cover
    ts = None


DEFAULT_TUSHARE_TOKEN = "23bcd49c307d6a664820333badbd8f5e879fa74f428037991bca9b13"


class TushareClientError(RuntimeError):
    pass


@dataclass
class TushareClient:
    token: str

    @classmethod
    def from_env(cls) -> "TushareClient":
        token = os.getenv("TUSHARE_TOKEN", "").strip() or DEFAULT_TUSHARE_TOKEN
        if not token:
            raise TushareClientError("未配置 TUSHARE_TOKEN，无法连接 Tushare。")
        return cls(token=token)

    def _get_pro(self):
        if ts is None:
            raise TushareClientError("当前环境未安装 tushare，请先安装依赖。")
        return ts.pro_api(self.token)

    def fetch_daily(self, ts_code: str, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
        try:
            pro = self._get_pro()
            df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        except Exception as exc:  # pragma: no cover
            raise TushareClientError(f"拉取 {ts_code} 日线数据失败: {exc}") from exc

        if df is None or df.empty:
            return pd.DataFrame()
        return df.copy()

    def fetch_daily_basic(self, ts_code: str, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
        try:
            pro = self._get_pro()
            df = pro.daily_basic(ts_code=ts_code, start_date=start_date, end_date=end_date, fields="ts_code,trade_date,turnover_rate")
        except Exception as exc:  # pragma: no cover
            raise TushareClientError(f"拉取 {ts_code} 基础指标失败: {exc}") from exc

        if df is None or df.empty:
            return pd.DataFrame()
        return df.copy()
