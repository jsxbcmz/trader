"""回测结果缓存：基于配置参数 hash 缓存回测结果，避免重复计算。"""

from __future__ import annotations

import hashlib
import json
import pickle
from pathlib import Path

from core.backtest.models import BacktestConfig, BacktestResult


def _config_cache_key(config: BacktestConfig) -> str:
    """根据回测配置的关键参数生成缓存键（SHA-256 前 16 位）"""
    key_dict = {
        "tdx_source": config.tdx_source,
        "stock_pool_name": config.stock_pool_name,
        "start_date": config.start_date,
        "end_date": config.end_date,
        "initial_capital": config.initial_capital,
        "position_size": config.position_size,
        "max_positions": config.max_positions,
        "commission_rate": config.commission_rate,
        "min_commission": config.min_commission,
        "stamp_tax_rate": config.stamp_tax_rate,
        "buy_timing": config.buy_timing.value,
        "sell_strategy_name": config.sell_strategy_name,
        "sell_strategy_params": json.dumps(
            config.sell_strategy_params, sort_keys=True, default=str,
        ),
    }
    raw = json.dumps(key_dict, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _cache_dir(root: Path) -> Path:
    """获取缓存目录路径"""
    return root / ".cache" / "backtest"


def get_cached_result(root: Path, config: BacktestConfig) -> BacktestResult | None:
    """尝试从缓存中加载回测结果

    Returns:
        BacktestResult 如果缓存命中，否则 None
    """
    cache_key = _config_cache_key(config)
    cache_file = _cache_dir(root) / f"{cache_key}.pkl"

    if not cache_file.exists():
        return None

    try:
        with open(cache_file, "rb") as fh:
            result = pickle.load(fh)
        if isinstance(result, BacktestResult):
            return result
    except Exception:
        # 缓存文件损坏，删除后返回 None
        cache_file.unlink(missing_ok=True)

    return None


def save_cached_result(root: Path, config: BacktestConfig, result: BacktestResult) -> Path:
    """将回测结果保存到缓存

    Returns:
        缓存文件路径
    """
    cache_key = _config_cache_key(config)
    cache_path = _cache_dir(root)
    cache_path.mkdir(parents=True, exist_ok=True)

    cache_file = cache_path / f"{cache_key}.pkl"
    with open(cache_file, "wb") as fh:
        pickle.dump(result, fh, protocol=pickle.HIGHEST_PROTOCOL)

    return cache_file


def clear_cache(root: Path) -> int:
    """清除所有回测缓存

    Returns:
        删除的缓存文件数量
    """
    cache_path = _cache_dir(root)
    if not cache_path.exists():
        return 0

    count = 0
    for cache_file in cache_path.glob("*.pkl"):
        cache_file.unlink(missing_ok=True)
        count += 1

    return count
