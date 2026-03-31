from __future__ import annotations

from pathlib import Path
import tempfile
import pandas as pd


DAILY_COLUMNS = ["date", "open", "close", "high", "low", "volume"]


def normalize_symbol(symbol: str) -> str:
    return str(symbol).zfill(6)


def get_daily_csv_path(stock_daily_data_dir: Path, symbol: str) -> Path:
    return stock_daily_data_dir / f"{normalize_symbol(symbol)}.csv"


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

    miss = set(DAILY_COLUMNS) - set(df.columns)
    if miss:
        raise ValueError(f"日线数据缺少字段: {sorted(miss)}")

    result = df[DAILY_COLUMNS].copy()
    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    for c in ["open", "close", "high", "low", "volume"]:
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
    """Load daily OHLCV from per-stock CSV.

    File name convention: {symbol}.csv where symbol is 6-digit.
    Directory convention: stock_daily_data/{symbol}.csv under project root.

    Expected columns:
      date,open,close,high,low,volume

    Notes:
      - User clarified: volume 实际为成交额（万元）。展示时可换算为“亿”：volume / 1e4
    """
    fp = get_daily_csv_path(stock_daily_data_dir, symbol)
    if not fp.exists():
        raise FileNotFoundError(f"找不到日线文件: {fp}")

    df = pd.read_csv(fp)

    required = {"date", "open", "close", "high", "low", "volume"}
    miss = required - set(df.columns)
    if miss:
        raise ValueError(f"{fp.name} 缺少字段: {sorted(miss)}")

    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    # Standardize to date,open,high,low,close,volume
    df = df[["date", "open", "high", "low", "close", "volume"]].copy()

    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df = df.dropna(subset=["open", "high", "low", "close"])  # keep rows with valid OHLC
    return df
