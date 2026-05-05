# core/filters.py —— A股基本面选股过滤模块（供 strategy_15x 使用）
#
# 提供：
#   filter_st(codes)                  → List[str]  排除ST/退市整理
#   filter_kcbj(codes)                → List[str]  排除科创板/北交所
#   filter_paused(codes)              → List[str]  排除今日停牌
#   filter_high_price(codes, max_price) → List[str]  排除高价股
#   filter_limit_up_today(codes)      → List[str]  排除今日涨停
#   get_recent_limit_up(codes, days)  → List[str]  近N天涨停过的股票
#
# 数据源：AKShare  ak.stock_zh_a_spot_em()（今日行情快照）
# 缓存：spot_snapshot_YYYYMMDD.csv（与 data_provider 共享缓存）

import logging
from datetime import date, timedelta
from pathlib import Path
from typing import List

import akshare as ak
import pandas as pd

from core.data_provider import _ak_call

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).parent.parent / "data"
_DATA_DIR.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# 今日行情快照（内部共享，避免重复拉取）
# ─────────────────────────────────────────────────────────────────────────────

_spot_cache: pd.DataFrame | None = None


def _get_spot() -> pd.DataFrame:
    """
    返回今日全A股行情快照 DataFrame。
    优先内存缓存 → 磁盘缓存 → AKShare 实时拉取。
    列：code, name, close, pct_change, volume, high_limit
    """
    global _spot_cache
    if _spot_cache is not None and not _spot_cache.empty:
        return _spot_cache

    today = date.today().strftime('%Y%m%d')
    cache_path = _DATA_DIR / f"spot_snapshot_{today}.csv"

    if cache_path.exists():
        try:
            df = pd.read_csv(cache_path, dtype={'代码': str, 'code': str})
            _spot_cache = _normalize_spot(df)
            return _spot_cache
        except Exception:
            pass

    try:
        df = _ak_call(ak.stock_zh_a_spot_em)
        df.to_csv(cache_path, index=False, encoding='utf-8')
        _spot_cache = _normalize_spot(df)
        return _spot_cache
    except Exception as e:
        logger.error(f"获取行情快照失败: {e}")
        return pd.DataFrame()


def _normalize_spot(df: pd.DataFrame) -> pd.DataFrame:
    """标准化快照字段名。"""
    col_map = {
        '代码':   'code',
        '名称':   'name',
        '最新价': 'close',
        '涨跌幅': 'pct_change',
        '成交量': 'volume',
        '涨停价': 'high_limit',
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

    if 'code' in df.columns:
        df['code'] = df['code'].astype(str).str.zfill(6)
    for col in ['close', 'pct_change', 'volume']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    return df.set_index('code') if 'code' in df.columns else df


# ─────────────────────────────────────────────────────────────────────────────
# 过滤函数
# ─────────────────────────────────────────────────────────────────────────────

def filter_st(codes: List[str]) -> List[str]:
    """
    排除名称中含 ST / 退市 / *ST 的股票。
    使用今日快照名称字段；快照获取失败时跳过过滤（保守策略）。
    """
    spot = _get_spot()
    if spot.empty or 'name' not in spot.columns:
        logger.warning("filter_st: 快照为空，跳过ST过滤")
        return codes

    st_mask = spot['name'].str.contains(r'ST|退市', na=False, regex=True)
    st_codes = set(spot[st_mask].index.astype(str).tolist())
    result = [c for c in codes if c not in st_codes]
    logger.debug(f"filter_st: {len(codes)} → {len(result)}（排除 {len(st_codes)} 只ST/退市）")
    return result


def filter_kcbj(codes: List[str]) -> List[str]:
    """
    排除科创板（688xxx）和北交所（8xxxxx / 4xxxxx）股票。
    仅根据代码前缀判断，无需网络请求。
    """
    result = [
        c for c in codes
        if not (
            str(c).startswith('688')       # 科创板
            or str(c).startswith('689')    # 科创板CDR
            or str(c).startswith('8')      # 北交所
            or str(c).startswith('4')      # 北交所老版
        )
    ]
    logger.debug(f"filter_kcbj: {len(codes)} → {len(result)}")
    return result


def filter_paused(codes: List[str]) -> List[str]:
    """
    排除今日停牌的股票（成交量 = 0）。
    """
    spot = _get_spot()
    if spot.empty or 'volume' not in spot.columns:
        logger.warning("filter_paused: 快照为空，跳过停牌过滤")
        return codes

    code_set = set(str(c) for c in codes)
    paused = set(
        spot[spot['volume'] == 0].index.astype(str).tolist()
    ) & code_set
    result = [c for c in codes if str(c) not in paused]
    logger.debug(f"filter_paused: {len(codes)} → {len(result)}（排除 {len(paused)} 只停牌）")
    return result


def filter_high_price(codes: List[str], max_price: float = 200.0) -> List[str]:
    """
    排除收盘价超过 max_price 的高价股。
    """
    spot = _get_spot()
    if spot.empty or 'close' not in spot.columns:
        logger.warning("filter_high_price: 快照为空，跳过高价过滤")
        return codes

    code_set = set(str(c) for c in codes)
    high = set(
        spot[spot['close'] > max_price].index.astype(str).tolist()
    ) & code_set
    result = [c for c in codes if str(c) not in high]
    logger.debug(f"filter_high_price(max={max_price}): {len(codes)} → {len(result)}")
    return result


def filter_limit_up_today(codes: List[str]) -> List[str]:
    """
    排除今日涨停的股票（涨幅 ≥ 9.9% 视为涨停，兼容 20cm 科创/创业板）。
    """
    spot = _get_spot()
    if spot.empty or 'pct_change' not in spot.columns:
        logger.warning("filter_limit_up_today: 快照为空，跳过今日涨停过滤")
        return codes

    code_set = set(str(c) for c in codes)
    limit_up = set(
        spot[spot['pct_change'] >= 9.9].index.astype(str).tolist()
    ) & code_set
    result = [c for c in codes if str(c) not in limit_up]
    logger.debug(f"filter_limit_up_today: {len(codes)} → {len(result)}（排除 {len(limit_up)} 只涨停）")
    return result


def get_recent_limit_up(codes: List[str], days: int = 30) -> List[str]:
    """
    返回过去 N 个交易日内有过涨停记录的股票列表。
    使用逐日行情快照缓存（已存在的复用，否则拉取）。

    注意：此函数调用较慢，对每只股票拉取历史日线数据判断涨停。
    """
    if not codes:
        return []

    logger.info(f"检查近 {days} 天涨停黑名单（{len(codes)} 只）...")
    start_date = (date.today() - timedelta(days=days + 10)).strftime('%Y%m%d')

    hit = []
    for code in codes:
        try:
            df = _ak_call(
                ak.stock_zh_a_hist,
                symbol=code,
                period='daily',
                start_date=start_date,
                end_date=date.today().strftime('%Y%m%d'),
                adjust='qfq',
            )
        except Exception:
            continue

        if df is None or df.empty:
            continue

        # 列名兼容
        df.columns = [c.lower().strip() for c in df.columns]
        close_col = next((c for c in ['收盘', 'close'] if c in df.columns), None)
        open_col  = next((c for c in ['开盘', 'open']  if c in df.columns), None)

        if close_col is None or open_col is None:
            continue

        df['close'] = pd.to_numeric(df[close_col], errors='coerce')
        df['open']  = pd.to_numeric(df[open_col],  errors='coerce')
        df['prev_close'] = df['close'].shift(1)

        # 涨停判断：当日涨幅 ≥ 9.9%
        df['pct'] = (df['close'] - df['prev_close']) / df['prev_close'].abs()
        recent = df.tail(days)
        if (recent['pct'] >= 0.099).any():
            hit.append(code)

    logger.info(f"近{days}天涨停黑名单: {len(hit)} 只")
    return hit
