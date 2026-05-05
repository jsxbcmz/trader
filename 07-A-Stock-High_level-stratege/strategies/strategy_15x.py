"""
策略1: 5年15倍小市值ROE策略

选股逻辑:
  1. 全A股过滤: ST / 科创 / 次新 / 停牌 / 涨停 / 高价
  2. 基本面: ROE > 15%, ROA > 10%
  3. 按市值升序排列，取最小市值 stock_num 只
  4. 过滤近 limit_days 天有过涨停的股票

注意: 使用当前TTM财务数据作近似，历史回测有偏差。
"""

import logging
from typing import List, Dict

import pandas as pd

from core.data_provider import (
    get_all_stocks, fetch_fundamentals_batch, get_stock_names_bulk
)
from core.filters import (
    filter_st, filter_kcbj, filter_paused, filter_high_price,
    filter_limit_up_today, get_recent_limit_up
)
from config import STRATEGY_15X

logger = logging.getLogger(__name__)

cfg = STRATEGY_15X


def select_stocks(universe: List[str] = None, apply_blacklist: bool = True) -> List[Dict]:
    """
    运行选股逻辑，返回推荐股票列表。

    Parameters
    ----------
    universe : 可选的股票池，默认全A股
    apply_blacklist : 是否剔除近30天涨停过的股票

    Returns
    -------
    List[Dict]: [
        {code, name, roe, roa, market_cap, reason}
    ]
    """
    logger.info("策略15x: 开始选股...")

    # Step1: 全市场股票
    if universe is None:
        universe = get_all_stocks()

    # Step2: 基础过滤
    codes = filter_kcbj(universe)
    codes = filter_st(codes)
    logger.info(f"过滤ST/北交所/科创后: {len(codes)}")

    # Step3: 获取基本面数据（ROE/ROA/市值）
    df_fund = fetch_fundamentals_batch(codes)
    if df_fund.empty:
        logger.warning("基本面数据为空，无法选股")
        return []

    # Step4: ROE > 15%, ROA > 10% 过滤
    roe_col = "roe" if "roe" in df_fund.columns else None
    roa_col = "roa" if "roa" in df_fund.columns else None

    if roe_col:
        df_fund = df_fund[pd.to_numeric(df_fund[roe_col], errors="coerce") > cfg["min_roe"]]
    if roa_col:
        df_fund = df_fund[pd.to_numeric(df_fund[roa_col], errors="coerce") > cfg["min_roa"]]

    logger.info(f"ROE/ROA过滤后: {len(df_fund)} 只")

    # Step5: 按市值升序（小市值优先）
    if "market_cap" in df_fund.columns:
        df_fund["market_cap"] = pd.to_numeric(df_fund["market_cap"], errors="coerce")
        df_fund = df_fund.dropna(subset=["market_cap"])
        df_fund = df_fund.sort_values("market_cap", ascending=True)

    candidates = df_fund["code"].tolist()

    # Step6: 停牌/涨停/高价过滤（实时数据，调用较慢，批量处理）
    candidates = filter_paused(candidates)
    candidates = filter_limit_up_today(candidates)
    candidates = filter_high_price(candidates, max_price=cfg["max_price"])

    # Step7: 近N天涨停黑名单
    if apply_blacklist:
        recent_lu = set(get_recent_limit_up(candidates, cfg["limit_days"]))
        candidates = [c for c in candidates if c not in recent_lu]

    # Step8: 取 top stock_num
    selected_codes = candidates[:cfg["stock_num"]]

    # Step9: 丰富信息
    names = get_stock_names_bulk(selected_codes)
    df_sel = df_fund[df_fund["code"].isin(selected_codes)].set_index("code")

    result = []
    for code in selected_codes:
        row = df_sel.loc[code] if code in df_sel.index else {}
        result.append({
            "code":       code,
            "name":       names.get(code, code),
            "roe":        round(float(row.get("roe", 0) or 0) * 100, 2),
            "roa":        round(float(row.get("roa", 0) or 0) * 100, 2),
            "market_cap": round(float(row.get("market_cap", 0) or 0), 1),
            "pe":         round(float(row.get("pe", 0) or 0), 2),
            "pb":         round(float(row.get("pb", 0) or 0), 2),
        })

    logger.info(f"策略15x 最终选出 {len(result)} 只")
    return result
