"""
core/backtest_engine.py — 月度轮动回测引擎

支持策略：
  - "15x"      : 小市值ROE策略（ROE>15%, ROA>10%, 小市值, 动量轮动）
  - "rotation" : 沪深300+中证500成分股，ROE≥10%筛选，月度动量轮动

run_backtest() 返回：
  {
    "metrics":    {total_return, annual_return, max_drawdown, sharpe, win_rate},
    "equity":     [{"date": "YYYY-MM-DD", "value": float}, ...],
    "benchmark":  [{"date": "YYYY-MM-DD", "value": float}, ...],
    "trades":     [{"date", "action", "code", "name", "price", "shares", "amount"}, ...],
    "disclaimer": str (optional),
    "error":      str (optional),
  }
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 内部工具
# ─────────────────────────────────────────────────────────────────────────────

def _prog(cb: Optional[Callable], pct: int, msg: str) -> None:
    # 控制台进度条
    bar_len = 30
    filled  = int(bar_len * pct / 100)
    bar     = "█" * filled + "░" * (bar_len - filled)
    print(f"\r[{bar}] {pct:3d}%  {msg}", end="", flush=True)
    if pct >= 100:
        print()  # 完成时换行
    if cb is not None:
        try:
            cb(pct, msg)
        except Exception:
            pass


def _get_momentum(
    code: str,
    date_str: str,
    hist_cache: Dict[str, pd.DataFrame],
    lookback_months: int = 3,
) -> Optional[float]:
    """
    计算截至 date_str 前 lookback_months 个月的价格动量（简单价格变化率）。
    date_str 格式：YYYYMMDD
    返回 None 表示数据不足。
    """
    df = hist_cache.get(code)
    if df is None or df.empty:
        return None

    try:
        end_dt   = pd.Timestamp(date_str)
        start_dt = end_dt - pd.DateOffset(months=lookback_months)
        sub = df[(df.index >= start_dt) & (df.index < end_dt)]
        if len(sub) < 20:
            return None
        p_start = float(sub["close"].iloc[0])
        p_end   = float(sub["close"].iloc[-1])
        if p_start <= 0:
            return None
        return (p_end - p_start) / p_start
    except Exception:
        return None


def _rebalance_dates(start_date: str, end_date: str) -> List[str]:
    """
    生成月初调仓日列表（每月第一个自然日，YYYYMMDD格式）。
    """
    s = pd.Timestamp(start_date)
    e = pd.Timestamp(end_date)
    dates = []
    cur = pd.Timestamp(year=s.year, month=s.month, day=1)
    while cur <= e:
        dates.append(cur.strftime("%Y%m%d"))
        if cur.month == 12:
            cur = pd.Timestamp(year=cur.year + 1, month=1, day=1)
        else:
            cur = pd.Timestamp(year=cur.year, month=cur.month + 1, day=1)
    return dates


def _get_price_on(df: pd.DataFrame, date_str: str) -> Optional[float]:
    """获取指定日期（或其后最近交易日）的收盘价。
    若无后续数据（如 end_date 为今日但行情尚未收盘），回退到最后一条已有数据。
    """
    if df is None or df.empty:
        return None
    dt = pd.Timestamp(date_str)
    sub = df[df.index >= dt]
    if not sub.empty:
        return float(sub["close"].iloc[0])
    # 回退：取 date_str 之前最后一个交易日价格（避免末期净值归零）
    sub_before = df[df.index < dt]
    if not sub_before.empty:
        return float(sub_before["close"].iloc[-1])
    return None


def _calc_metrics(
    equity: pd.Series,
    initial_cash: float,
    trading_days_per_year: int = 245,
) -> Dict:
    """从净值序列计算回测指标。支持月度或日度序列（自动推断）。"""
    if equity.empty or len(equity) < 2:
        return {
            "total_return": 0.0,
            "annual_return": 0.0,
            "max_drawdown":  0.0,
            "sharpe":        0.0,
            "win_rate":      0.0,
            "duration_days": 0,
        }

    duration_days = int((equity.index[-1] - equity.index[0]).days)
    n_years = duration_days / 365.25

    total_return = float((equity.iloc[-1] / initial_cash - 1) * 100)
    if n_years > 0:
        annual_return = float(((equity.iloc[-1] / initial_cash) ** (1 / n_years) - 1) * 100)
    else:
        annual_return = 0.0

    rolling_max = equity.cummax()
    drawdown = (equity - rolling_max) / rolling_max
    max_drawdown = float(drawdown.min() * 100)

    # 推断频率：月度序列用 sqrt(12)，日度用 sqrt(245)
    avg_gap_days = duration_days / max(len(equity) - 1, 1)
    periods_per_year = 12 if avg_gap_days > 15 else trading_days_per_year

    period_ret = equity.pct_change().dropna()
    if period_ret.std() > 0:
        sharpe = float((period_ret.mean() / period_ret.std()) * np.sqrt(periods_per_year))
    else:
        sharpe = 0.0

    # 月度胜率
    monthly = equity.resample("ME").last()
    monthly_ret = monthly.pct_change().dropna()
    win_rate = float((monthly_ret > 0).mean() * 100) if len(monthly_ret) > 0 else 0.0

    return {
        "total_return":  round(total_return, 2),
        "annual_return": round(annual_return, 2),
        "max_drawdown":  round(max_drawdown, 2),
        "sharpe":        round(sharpe, 3),
        "win_rate":      round(win_rate, 1),
        "duration_days": duration_days,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 15x策略：筛选标的池
# ─────────────────────────────────────────────────────────────────────────────

def _filter_15x_pool(
    all_codes: List[str],
    df_fund: pd.DataFrame,
    cfg: Dict,
) -> List[str]:
    """应用 ROE/ROA/市值 过滤，返回候选池（已排好序）。"""
    from core.filters import filter_st, filter_kcbj

    codes = filter_kcbj(all_codes)
    codes = filter_st(codes)

    if df_fund.empty:
        return codes[:cfg.get("stock_num", 20) * 3]

    df = df_fund[df_fund["code"].isin(codes)].copy()
    df["roe"]        = pd.to_numeric(df.get("roe",        pd.Series(dtype=float)), errors="coerce")
    df["roa"]        = pd.to_numeric(df.get("roa",        pd.Series(dtype=float)), errors="coerce")
    df["market_cap"] = pd.to_numeric(df.get("market_cap", pd.Series(dtype=float)), errors="coerce")

    if "roe" in df.columns:
        df = df[df["roe"] > cfg.get("min_roe", 0.15)]
    if "roa" in df.columns:
        df = df[df["roa"] > cfg.get("min_roa", 0.10)]
    if "market_cap" in df.columns:
        max_price = cfg.get("max_price", 200.0)
        df = df.dropna(subset=["market_cap"])
        df = df.sort_values("market_cap", ascending=True)

    return df["code"].tolist()


# ─────────────────────────────────────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────────────────────────────────────

def run_backtest(
    strategy_id:   str,
    start_date:    str,
    end_date:      str,
    initial_cash:  float,
    commission:    float   = 0.0003,
    stock_num:     int     = 10,
    progress_cb:   Optional[Callable] = None,
) -> Dict:
    """
    运行月度轮动回测。

    Parameters
    ----------
    strategy_id  : "15x" 或 "rotation"
    start_date   : "YYYYMMDD"
    end_date     : "YYYYMMDD"
    initial_cash : 初始资金（元）
    commission   : 单边手续费率（买卖各一次）
    stock_num    : 持股数量
    progress_cb  : 进度回调 (pct: int, msg: str) -> None
    """
    from core.data_provider import (
        get_all_stocks, fetch_fundamentals_batch, get_stock_names_bulk,
        fetch_history_em, fetch_index_history, get_index_constituents,
    )
    from config import STRATEGY_15X

    _prog(progress_cb, 5, "初始化...")

    # ── 1. 确定股票池 ──────────────────────────────────────────────────────────
    _prog(progress_cb, 10, "获取股票池...")
    if strategy_id == "rotation":
        hs300 = get_index_constituents("000300")
        zz500 = get_index_constituents("000905")
        pool  = list(dict.fromkeys(hs300 + zz500))   # 合并去重，沪深300在前
        if not pool:
            return {"error": "无法获取成分股（沪深300 / 中证500）"}
        cfg = {"stock_num": stock_num, "min_roe": 0.10, "min_roa": 0.10, "max_price": 99999}
        disclaimer = "大盘轮动：沪深300+中证500成分股，ROE≥10% / ROA≥10% 筛选，月度按过去3月动量取前N只等权换仓"
    else:
        pool = get_all_stocks()
        if not pool:
            return {"error": "无法获取全市场股票列表"}
        cfg = dict(STRATEGY_15X)
        cfg["stock_num"] = stock_num
        disclaimer = "注意：使用当前TTM财务数据作历史近似，回测存在未来数据偏差。"

    # ── 2. 基本面过滤 ─────────────────────────────────────────────────────────
    target_pool = pool
    df_fund = pd.DataFrame()
    if strategy_id == "15x":
        _prog(progress_cb, 20, "获取基本面数据（可能需要较长时间）...")
        df_fund = fetch_fundamentals_batch(pool)
        target_pool = _filter_15x_pool(pool, df_fund, cfg)
        if not target_pool:
            return {"error": "ROE/ROA 筛选后无满足条件的股票"}
        target_pool = target_pool[:min(len(target_pool), stock_num * 5)]
    elif strategy_id == "rotation":
        _prog(progress_cb, 20, "获取基本面数据（大盘轮动筛选）...")
        df_fund = fetch_fundamentals_batch(pool)
        if not df_fund.empty:
            from core.filters import filter_st, filter_kcbj
            rot_codes = filter_kcbj(pool)
            rot_codes = filter_st(rot_codes)
            df_r = df_fund[df_fund["code"].isin(rot_codes)].copy()
            df_r["roe"] = pd.to_numeric(df_r.get("roe", pd.Series(dtype=float)), errors="coerce")
            df_r["roa"] = pd.to_numeric(df_r.get("roa", pd.Series(dtype=float)), errors="coerce")
            df_r["market_cap"] = pd.to_numeric(df_r.get("market_cap", pd.Series(dtype=float)), errors="coerce")
            if "roe" in df_r.columns:
                df_r = df_r[df_r["roe"] > cfg.get("min_roe", 0.10)]
            if "roa" in df_r.columns:
                df_r = df_r[df_r["roa"] > cfg.get("min_roa", 0.10)]
            df_r = df_r.dropna(subset=["market_cap"])
            df_r = df_r.sort_values("market_cap", ascending=False)   # 大市值优先
            target_pool = df_r["code"].tolist()
            if not target_pool:
                # 基本面数据不完整时降级为全成分股
                target_pool = pool
        target_pool = target_pool[:min(len(target_pool), stock_num * 5)]

    # 北交所（920xxx / 430xxx / 830xxx）腾讯和东方财富均不可用，直接跳过
    _BJ_PREFIXES = ("92", "43", "83")
    bj_skipped = [c for c in target_pool if c[:2] in _BJ_PREFIXES]
    target_pool = [c for c in target_pool if c[:2] not in _BJ_PREFIXES]
    if bj_skipped:
        logger.info(f"跳过北交所股票 {len(bj_skipped)} 只（数据源不支持）")

    _prog(progress_cb, 35, f"候选池 {len(target_pool)} 只（已跳过北交所 {len(bj_skipped)} 只），下载历史K线...")

    # ── 3. 并发下载历史K线（限速避免腾讯API限流）────────────────────────────
    hist_cache: Dict[str, pd.DataFrame] = {}
    completed = [0]
    failed    = [0]
    import threading
    _rate_lock = threading.Semaphore(3)   # 最多3个并发请求

    def _download_one(code: str):
        import time as _time
        with _rate_lock:
            df_h = fetch_history_em(code, start=start_date, end=end_date)
            _time.sleep(0.3)   # 每个请求间隔300ms，避免腾讯限速
        completed[0] += 1
        if df_h is None or df_h.empty:
            failed[0] += 1
        n = len(target_pool)
        if n > 0 and (completed[0] % 3 == 0 or completed[0] == n):
            pct = 35 + int(completed[0] / n * 25)
            ok  = completed[0] - failed[0]
            _prog(progress_cb, pct,
                  f"下载K线 {completed[0]}/{n}  ✓{ok} ✗{failed[0]}")
        return code, df_h

    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=6) as pool_ex:
        futures = {pool_ex.submit(_download_one, code): code for code in target_pool}
        for future in as_completed(futures):
            code, df_h = future.result()
            if df_h is not None and not df_h.empty:
                hist_cache[code] = df_h

    valid_pool = [c for c in target_pool if c in hist_cache and len(hist_cache[c]) >= 60]
    if not valid_pool:
        return {"error": "无有效历史K线数据"}

    # ── 4. 月度调仓（动量轮动）────────────────────────────────────────────────
    _prog(progress_cb, 65, "开始月度模拟（动量轮动）...")

    rebal_dates = _rebalance_dates(start_date, end_date)
    cash    = float(initial_cash)
    holdings: Dict[str, int] = {}   # code → shares
    hold_prices: Dict[str, float] = {}  # code → 买入均价

    equity_records: List[Dict] = []
    trades: List[Dict] = []

    names = get_stock_names_bulk(valid_pool)

    for rb_idx, rb_date in enumerate(rebal_dates):
        pct = 65 + int(rb_idx / max(len(rebal_dates), 1) * 25)
        _prog(progress_cb, pct, f"调仓 {rb_date}...")

        # 计算各股票动量得分
        scores: Dict[str, float] = {}
        for code in valid_pool:
            m = _get_momentum(code, rb_date, hist_cache, lookback_months=3)
            if m is not None:
                scores[code] = m

        # 按动量降序选 stock_num 只
        # 若得分股票数 <= stock_num，则只持有一半，保证有轮动空间
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        effective_n = stock_num if len(ranked) > stock_num else max(1, len(ranked) // 2)
        new_holdings = [c for c, _ in ranked[:effective_n]]

        # ── 全部卖出当前持仓，等全换仓 ──
        for code in list(holdings.keys()):
            price = _get_price_on(hist_cache.get(code), rb_date)
            if price is None or price <= 0:
                continue
            shares = holdings.pop(code, 0)
            if shares <= 0:
                continue
            proceeds = shares * price * (1 - commission)
            cash += proceeds
            trades.append({
                "date":   rb_date,
                "action": "卖出",
                "code":   code,
                "name":   names.get(code, code),
                "price":  round(price, 3),
                "shares": shares,
                "amount": round(proceeds, 2),
            })

        # ── 等权买入新名单全部股票 ──
        buy_codes = new_holdings
        if buy_codes and cash > 0:
            alloc = cash / len(buy_codes)
            for code in buy_codes:
                price = _get_price_on(hist_cache.get(code), rb_date)
                if price is None or price <= 0:
                    continue
                lots = int(alloc / (price * (1 + commission)) / 100)
                if lots <= 0:
                    continue
                shares = lots * 100
                cost = shares * price * (1 + commission)
                if cost > cash:
                    continue
                cash -= cost
                holdings[code] = holdings.get(code, 0) + shares
                hold_prices[code] = price
                trades.append({
                    "date":   rb_date,
                    "action": "买入",
                    "code":   code,
                    "name":   names.get(code, code),
                    "price":  round(price, 3),
                    "shares": shares,
                    "amount": round(cost, 2),
                })

        # 计算本月末净值（取下一调仓日前一天，或当日）
        next_date = rebal_dates[rb_idx + 1] if rb_idx + 1 < len(rebal_dates) else end_date
        holding_value = 0.0
        for code, shares in holdings.items():
            price = _get_price_on(hist_cache.get(code), next_date)
            if price:
                holding_value += shares * price
        total = cash + holding_value
        equity_records.append({"date": rb_date[:4] + "-" + rb_date[4:6] + "-" + rb_date[6:], "value": total})

    _prog(progress_cb, 92, "获取基准数据...")

    # ── 5. 基准：沪深300 ─────────────────────────────────────────────────────
    benchmark_records: List[Dict] = []
    try:
        bm_df = fetch_index_history("000300", start=start_date, end=end_date)
        if not bm_df.empty and "close" in bm_df.columns:
            bm_close = bm_df["close"].resample("MS").first()
            if not bm_close.empty:
                bm_base = float(bm_close.iloc[0])
                for dt, val in bm_close.items():
                    benchmark_records.append({
                        "date":  dt.strftime("%Y-%m-%d"),
                        "value": round(initial_cash * val / bm_base, 2),
                    })
    except Exception as e:
        logger.warning(f"基准数据获取失败: {e}")

    _prog(progress_cb, 97, "计算指标...")

    # ── 6. 计算指标 ──────────────────────────────────────────────────────────
    eq_series = pd.Series(
        [r["value"] for r in equity_records],
        index=pd.to_datetime([r["date"] for r in equity_records]),
    )
    metrics = _calc_metrics(eq_series, initial_cash)

    _prog(progress_cb, 100, "完成！")

    held_codes = list(dict.fromkeys(t["code"] for t in trades if t.get("action") == "买入"))
    result = {
        "metrics":    metrics,
        "equity":     equity_records,
        "benchmark":  benchmark_records,
        "trades":     trades,
        "held_codes": held_codes,
        "disclaimer": disclaimer,
    }

    # 自动保存到 output/<timestamp>/
    try:
        save_run_output(result, strategy_id, start_date, end_date, initial_cash)
    except Exception as e:
        logger.warning(f"保存回测结果失败: {e}")

    return result


# ─────────────────────────────────────────────────────────────────────────────
# 回测结果持久化
# ─────────────────────────────────────────────────────────────────────────────

def save_run_output(
    result: Dict,
    strategy_id: str,
    start_date: str,
    end_date: str,
    initial_cash: float,
) -> str:
    """
    将回测结果保存到 output/<timestamp>/ 目录。
    目录名格式：{strategy}_{YYYYMMDD_HHMMSS}
    返回保存路径字符串。
    """
    from pathlib import Path

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_id = f"{strategy_id}_{ts}"
    out_dir = Path(__file__).parent.parent / "output" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    m = result.get("metrics", {})

    # metrics.csv
    pd.DataFrame([{
        "run_id":        run_id,
        "strategy":      strategy_id,
        "start_date":    start_date,
        "end_date":      end_date,
        "initial_cash":  initial_cash,
        "total_return":  round(m.get("total_return", 0), 2),
        "annual_return": round(m.get("annual_return", 0), 2),
        "max_drawdown":  round(m.get("max_drawdown", 0), 2),
        "sharpe":        round(m.get("sharpe", 0), 3),
        "win_rate":      round(m.get("win_rate", 0), 2),
        "duration_days": m.get("duration_days", 0),
        "saved_at":      datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }]).to_csv(out_dir / "metrics.csv", index=False)

    # portfolio_equity_daily.csv
    equity = result.get("equity", [])
    if equity:
        pd.DataFrame(equity).rename(columns={"value": "equity"}).to_csv(
            out_dir / "portfolio_equity_daily.csv", index=False
        )

    # portfolio_trades.csv
    trades = result.get("trades", [])
    if trades:
        pd.DataFrame(trades).to_csv(out_dir / "portfolio_trades.csv", index=False)

    # summary.csv — 各标的交易次数/盈亏统计
    if trades:
        rows = []
        for code in dict.fromkeys(t["code"] for t in trades):
            code_trades = [t for t in trades if t["code"] == code]
            buys  = [t for t in code_trades if t["action"] == "买入"]
            sells = [t for t in code_trades if t["action"] == "卖出"]
            pairs = min(len(buys), len(sells))
            wins  = sum(
                1 for b, s in zip(buys[:pairs], sells[:pairs])
                if s["price"] > b["price"]
            )
            total_ret = 0.0
            if buys and sells:
                buy_cost  = sum(t["amount"] for t in buys)
                sell_rev  = sum(t["amount"] for t in sells)
                if buy_cost > 0:
                    total_ret = round((sell_rev - buy_cost) / buy_cost * 100, 2)
            rows.append({
                "code":           code,
                "trade_count":    len(code_trades),
                "win_count":      wins,
                "loss_count":     max(0, pairs - wins),
                "win_rate_pct":   round(wins / pairs * 100, 1) if pairs else 0,
                "total_return_pct": total_ret,
            })
        pd.DataFrame(rows).to_csv(out_dir / "summary.csv", index=False)

    logger.info(f"回测结果已保存: output/{run_id}/")
    return str(out_dir)
