"""Data IO — SQLite-backed with in-memory cache.

所有读写操作通过 ``core.data.database`` 的全局实例访问 SQLite。
函数签名保持向后兼容：旧的 ``stock_daily_data_dir`` / ``stocklist_csv``
参数保留但不再使用（兼容期），实际走数据库。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from core.data.database import get_market_db


DAILY_COLUMNS = ["date", "open", "close", "high", "low", "volume", "turnover_rate"]

# 模块级别的数据缓存，减少重复数据库IO
_daily_data_cache: dict[str, pd.DataFrame] = {}
_CACHE_MAX_SIZE = 200


def normalize_symbol(symbol: str) -> str:
    return str(symbol).zfill(6)


# ── 股票列表 ─────────────────────────────────────────────

def load_stock_list(stocklist_csv: Path) -> pd.DataFrame:
    """从数据库加载股票列表。

    参数 stocklist_csv 保留兼容但不再使用。
    返回的 DataFrame 列名与原 CSV 保持一致（含 '涉及概念'、'涉及行业'）。
    """
    market_db = get_market_db()
    df = market_db.read_df(
        "SELECT symbol, ts_code, name, area, industry, market, "
        "concepts, ths_industry FROM stock_list ORDER BY symbol"
    )
    if df.empty:
        return pd.DataFrame(columns=["ts_code", "symbol", "name", "area", "industry"])

    df["symbol"] = df["symbol"].astype(str).str.zfill(6)
    df.rename(columns={"concepts": "涉及概念", "ths_industry": "涉及行业"}, inplace=True)
    return df


# ── 日线归一化（纯函数，不涉及存储） ──────────────────────

def normalize_daily_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=DAILY_COLUMNS)

    if "turnover_rate" not in df.columns:
        df = df.copy()
        df["turnover_rate"] = None

    miss = set(DAILY_COLUMNS) - set(df.columns)
    if miss:
        raise ValueError(f"日线数据缺少字段: {sorted(miss)}")

    result = df[DAILY_COLUMNS].copy()
    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    for col in ["open", "close", "high", "low", "volume", "turnover_rate"]:
        result[col] = pd.to_numeric(result[col], errors="coerce")

    result = result.dropna(subset=["date", "open", "close", "high", "low"])
    result["date"] = result["date"].dt.strftime("%Y-%m-%d")
    result = result.drop_duplicates(subset=["date"], keep="last")
    result = result.sort_values("date").reset_index(drop=True)
    return result


# ── 个股日线 ─────────────────────────────────────────────

def get_last_trade_date(stock_daily_data_dir: Path, symbol: str) -> pd.Timestamp | None:
    """查询某只股票本地最新交易日。"""
    market_db = get_market_db()
    date_str = market_db.get_last_trade_date(normalize_symbol(symbol))
    if date_str is None:
        return None
    return pd.Timestamp(date_str)


def save_daily_csv(stock_daily_data_dir: Path, symbol: str, df: pd.DataFrame):
    """将日线数据写入数据库。"""
    normalized = normalize_daily_dataframe(df)
    if normalized.empty:
        return

    market_db = get_market_db()
    symbol = normalize_symbol(symbol)
    market_db.bulk_upsert_stock_daily(symbol, normalized)

    cache_key = f"db:{symbol}"
    _daily_data_cache.pop(cache_key, None)


def load_raw_daily_csv(stock_daily_data_dir: Path, symbol: str) -> pd.DataFrame:
    """从数据库加载原始日线（不做额外归一化处理）。"""
    market_db = get_market_db()
    symbol = normalize_symbol(symbol)
    df = market_db.read_df(
        "SELECT date, open, close, high, low, volume, turnover_rate "
        "FROM stock_daily WHERE symbol = ? ORDER BY date",
        [symbol],
    )
    if df.empty:
        return pd.DataFrame(columns=DAILY_COLUMNS)
    return df


def load_daily_csv(stock_daily_data_dir: Path, symbol: str) -> pd.DataFrame:
    """从数据库加载日线 OHLCV（带缓存）。

    Notes:
      - volume 实际为成交额（万元），展示时可换算为"亿"：volume / 1e4
      - 使用模块级别缓存减少重复数据库IO
    """
    symbol = normalize_symbol(symbol)
    cache_key = f"db:{symbol}"

    if cache_key in _daily_data_cache:
        return _daily_data_cache[cache_key].copy()

    market_db = get_market_db()
    df = market_db.read_df(
        "SELECT date, open, high, low, close, volume, turnover_rate "
        "FROM stock_daily WHERE symbol = ? ORDER BY date",
        [symbol],
    )

    if df.empty:
        raise FileNotFoundError(f"数据库中找不到 {symbol} 的日线数据")

    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    for col in ["open", "high", "low", "close", "volume", "turnover_rate"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["open", "high", "low", "close"])

    if len(_daily_data_cache) >= _CACHE_MAX_SIZE:
        keys_to_remove = list(_daily_data_cache.keys())[:_CACHE_MAX_SIZE // 2]
        for key in keys_to_remove:
            del _daily_data_cache[key]

    _daily_data_cache[cache_key] = df
    return df.copy()


def clear_daily_data_cache():
    """清除数据缓存。"""
    _daily_data_cache.clear()


# ── 指数日线 ─────────────────────────────────────────────

def load_index_csv(stock_daily_data_dir: Path, ts_code: str) -> pd.DataFrame:
    """从数据库加载指数日线。"""
    market_db = get_market_db()
    df = market_db.read_df(
        "SELECT date, open, close, high, low, volume, turnover_rate "
        "FROM index_daily WHERE ts_code = ? ORDER BY date",
        [ts_code],
    )
    if df.empty:
        return pd.DataFrame(columns=DAILY_COLUMNS)

    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])
    return df


# ── OAMV ─────────────────────────────────────────────────

def load_oamv_csv(stock_daily_data_dir: Path) -> pd.DataFrame:
    """从数据库加载 OAMV 活跃市值虚拟K线。"""
    market_db = get_market_db()
    df = market_db.read_df(
        "SELECT date, open, close, high, low FROM oamv_daily ORDER BY date"
    )
    if df.empty:
        return pd.DataFrame(columns=DAILY_COLUMNS)

    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])
    return df


# ── 行业日线 ─────────────────────────────────────────────

def load_industry_csv(industry_data_dir: Path, ts_code: str) -> pd.DataFrame:
    """从数据库加载行业日线。"""
    market_db = get_market_db()
    df = market_db.read_df(
        "SELECT date, open, close, high, low, volume, turnover_rate "
        "FROM industry_daily WHERE ts_code = ? ORDER BY date",
        [ts_code],
    )
    if df.empty:
        return pd.DataFrame(columns=DAILY_COLUMNS)

    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])
    return df


# ── 其他 ─────────────────────────────────────────────────

def load_industry_mapping(root: Path) -> dict[str, str]:
    import json
    fp = root / "industry_mapping.json"
    if not fp.exists():
        return {}
    with open(fp, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}
