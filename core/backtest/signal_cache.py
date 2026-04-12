"""选股信号表缓存：基于选股参数 hash 缓存预计算的信号表，避免重复计算。"""

from __future__ import annotations

import hashlib
import json
import pickle
from pathlib import Path


def _signal_cache_key(
    tdx_source: str,
    stock_pool_name: str,
    start_date: str,
    end_date: str,
) -> str:
    """根据选股相关参数生成信号缓存键（SHA-256 前 16 位）

    信号表只与选股条件、股票池、时间范围有关，与资金参数/卖出策略无关。
    """
    key_dict = {
        "tdx_source": (tdx_source or "").strip(),
        "stock_pool_name": stock_pool_name,
        "start_date": start_date,
        "end_date": end_date,
    }
    raw = json.dumps(key_dict, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _cache_dir(root: Path) -> Path:
    return root / ".cache" / "backtest"


def get_cached_signals(
    root: Path,
    tdx_source: str,
    stock_pool_name: str,
    start_date: str,
    end_date: str,
) -> dict[str, list[dict[str, str]]] | None:
    """尝试从缓存加载预计算的信号表

    Returns:
        信号表 {date_str: [{symbol, name}, ...]} 或 None
    """
    cache_key = _signal_cache_key(tdx_source, stock_pool_name, start_date, end_date)
    cache_file = _cache_dir(root) / f"signals_{cache_key}.pkl"

    if not cache_file.exists():
        return None

    try:
        with open(cache_file, "rb") as fh:
            data = pickle.load(fh)
        if isinstance(data, dict):
            return data
    except Exception:
        cache_file.unlink(missing_ok=True)

    return None


def save_cached_signals(
    root: Path,
    tdx_source: str,
    stock_pool_name: str,
    start_date: str,
    end_date: str,
    signal_table: dict[str, list[dict[str, str]]],
) -> Path:
    """将预计算的信号表保存到缓存

    Returns:
        缓存文件路径
    """
    cache_key = _signal_cache_key(tdx_source, stock_pool_name, start_date, end_date)
    cache_path = _cache_dir(root)
    cache_path.mkdir(parents=True, exist_ok=True)

    cache_file = cache_path / f"signals_{cache_key}.pkl"
    with open(cache_file, "wb") as fh:
        pickle.dump(signal_table, fh, protocol=pickle.HIGHEST_PROTOCOL)

    return cache_file
