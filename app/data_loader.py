from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import tempfile
import pandas as pd


DAILY_COLUMNS = ["date", "open", "close", "high", "low", "volume", "turnover_rate"]

# 模块级别的数据缓存，减少重复磁盘IO
_daily_data_cache: dict[str, pd.DataFrame] = {}
_CACHE_MAX_SIZE = 200  # 最多缓存200只股票的数据


def normalize_symbol(symbol: str) -> str:
    return str(symbol).zfill(6)


def get_daily_csv_path(stock_daily_data_dir: Path, symbol: str) -> Path:
    return stock_daily_data_dir / f"{normalize_symbol(symbol)}.csv"


def get_index_csv_path(stock_daily_data_dir: Path, ts_code: str) -> Path:
    tag = ts_code.replace(".", "_")
    return stock_daily_data_dir / f"index_{tag}.csv"


def get_industry_csv_path(industry_data_dir: Path, ts_code: str) -> Path:
    tag = ts_code.replace(".", "_")
    return industry_data_dir / f"{tag}.csv"


def load_stock_list(stocklist_csv: Path) -> pd.DataFrame:
    """Load stock list CSV.

    Expected columns (at least): ts_code,symbol,name,area,industry
    """
    df = pd.read_csv(stocklist_csv, dtype={"symbol": str, "ts_code": str})
    if "symbol" not in df.columns:
        raise ValueError("stocklist.csv 缺少 symbol 列")

    df["symbol"] = df["symbol"].astype(str).str.zfill(6)
    return df


def load_raw_daily_csv(stock_daily_data_dir: Path, symbol: str) -> pd.DataFrame:
    fp = get_daily_csv_path(stock_daily_data_dir, symbol)
    if not fp.exists():
        return pd.DataFrame(columns=DAILY_COLUMNS)
    return pd.read_csv(fp)


def normalize_daily_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=DAILY_COLUMNS)

    # turnover_rate 是新增字段，旧数据可能没有，兼容处理
    if "turnover_rate" not in df.columns:
        df = df.copy()
        df["turnover_rate"] = None

    miss = set(DAILY_COLUMNS) - set(df.columns)
    if miss:
        raise ValueError(f"日线数据缺少字段: {sorted(miss)}")

    result = df[DAILY_COLUMNS].copy()
    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    for c in ["open", "close", "high", "low", "volume", "turnover_rate"]:
        result[c] = pd.to_numeric(result[c], errors="coerce")

    result = result.dropna(subset=["date", "open", "close", "high", "low"])
    result["date"] = result["date"].dt.strftime("%Y-%m-%d")
    result = result.drop_duplicates(subset=["date"], keep="last")
    result = result.sort_values("date").reset_index(drop=True)
    return result


def get_last_trade_date(stock_daily_data_dir: Path, symbol: str) -> pd.Timestamp | None:
    df = load_raw_daily_csv(stock_daily_data_dir, symbol)
    if df.empty or "date" not in df.columns:
        return None

    dates = pd.to_datetime(df["date"], errors="coerce").dropna()
    if dates.empty:
        return None
    return dates.max()


def save_daily_csv(stock_daily_data_dir: Path, symbol: str, df: pd.DataFrame) -> Path:
    normalized = normalize_daily_dataframe(df)
    stock_daily_data_dir.mkdir(parents=True, exist_ok=True)
    target = get_daily_csv_path(stock_daily_data_dir, symbol)

    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".csv", dir=stock_daily_data_dir, encoding="utf-8-sig", newline="") as tmp:
        normalized.to_csv(tmp.name, index=False)
        temp_path = Path(tmp.name)

    temp_path.replace(target)
    return target


def load_daily_csv(stock_daily_data_dir: Path, symbol: str) -> pd.DataFrame:
    """Load daily OHLCV from per-stock CSV with caching.

    File name convention: {symbol}.csv where symbol is 6-digit.
    Directory convention: stock_daily_data/{symbol}.csv under project root.

    Expected columns:
      date,open,close,high,low,volume

    Notes:
      - User clarified: volume 实际为成交额（万元）。展示时可换算为"亿"：volume / 1e4
      - 使用模块级别缓存减少重复磁盘IO
    """
    # 使用路径和symbol作为缓存键
    cache_key = f"{stock_daily_data_dir}:{symbol}"
    
    if cache_key in _daily_data_cache:
        return _daily_data_cache[cache_key].copy()
    
    fp = get_daily_csv_path(stock_daily_data_dir, symbol)
    if not fp.exists():
        raise FileNotFoundError(f"找不到日线文件: {fp}")

    df = pd.read_csv(fp)

    required = {"date", "open", "close", "high", "low", "volume"}
    miss = required - set(df.columns)
    if miss:
        raise ValueError(f"{fp.name} 缺少字段: {sorted(miss)}")

    # 兼容旧数据：没有 turnover_rate 列时补 NaN
    if "turnover_rate" not in df.columns:
        df["turnover_rate"] = None

    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    df = df[["date", "open", "high", "low", "close", "volume", "turnover_rate"]].copy()

    for c in ["open", "high", "low", "close", "volume", "turnover_rate"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df = df.dropna(subset=["open", "high", "low", "close"])  # keep rows with valid OHLC
    
    # 缓存数据（控制缓存大小）
    if len(_daily_data_cache) >= _CACHE_MAX_SIZE:
        # 简单的LRU策略：清除一半旧数据
        keys_to_remove = list(_daily_data_cache.keys())[:_CACHE_MAX_SIZE // 2]
        for k in keys_to_remove:
            del _daily_data_cache[k]
    
    _daily_data_cache[cache_key] = df
    return df.copy()


def clear_daily_data_cache():
    """清除数据缓存"""
    _daily_data_cache.clear()


def load_index_csv(stock_daily_data_dir: Path, ts_code: str) -> pd.DataFrame:
    fp = get_index_csv_path(stock_daily_data_dir, ts_code)
    if not fp.exists():
        return pd.DataFrame(columns=DAILY_COLUMNS)

    df = pd.read_csv(fp)
    required = {"date", "open", "close", "high", "low"}
    miss = required - set(df.columns)
    if miss:
        return pd.DataFrame(columns=DAILY_COLUMNS)

    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    for c in ["open", "high", "low", "close"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])
    return df


def load_oamv_csv(stock_daily_data_dir: Path) -> pd.DataFrame:
    """加载 OAMV 活跃市值 CSV（基于中证A股 930903 计算的虚拟K线）。"""
    fp = stock_daily_data_dir / "oamv_930903_CSI.csv"
    if not fp.exists():
        return pd.DataFrame(columns=DAILY_COLUMNS)

    df = pd.read_csv(fp)
    required = {"date", "open", "close", "high", "low"}
    miss = required - set(df.columns)
    if miss:
        return pd.DataFrame(columns=DAILY_COLUMNS)

    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])
    return df


def load_industry_csv(industry_data_dir: Path, ts_code: str) -> pd.DataFrame:
    fp = get_industry_csv_path(industry_data_dir, ts_code)
    if not fp.exists():
        return pd.DataFrame(columns=DAILY_COLUMNS)

    df = pd.read_csv(fp)
    required = {"date", "open", "close", "high", "low"}
    miss = required - set(df.columns)
    if miss:
        return pd.DataFrame(columns=DAILY_COLUMNS)

    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    for c in ["open", "high", "low", "close"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])
    return df


def load_industry_mapping(root: Path) -> dict[str, str]:
    import json
    fp = root / "industry_mapping.json"
    if not fp.exists():
        return {}
    with open(fp, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}
