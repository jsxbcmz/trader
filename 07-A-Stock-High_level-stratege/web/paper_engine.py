"""
模拟交易引擎 — 账户状态管理 + 信号生成

账户数据存储在 data/paper_account.json：
  {
    "initial_cash": 100000,
    "cash": 100000,
    "positions": [
        {"code": "600519", "name": "贵州茅台", "shares": 100,
         "cost": 1688.0, "current_price": 1700.0, "change_pct": 0.71,
         "buy_date": "2026-04-17"}
    ],
    "trades": [
        {"time": "2026-04-17 10:03:22", "code": "600519", "name": "贵州茅台",
         "action": "买入", "price": 1688.0, "shares": 100,
         "amount": 169056.8, "status": "已成交", "source": "手动"}
    ],
    "nav_history": [
        {"date": "2026-04-17", "nav": 1.0, "total": 100000}
    ],
    "created_at": "2026-04-17",
    "updated_at": "2026-04-17 10:00:00"
  }
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

_BASE = Path(__file__).resolve().parents[1]
_ACCOUNT_PATH = _BASE / "data" / "paper_account.json"

# 确保 core/strategies/config 可被导入
sys.path.insert(0, str(_BASE))

DEFAULT_INITIAL_CASH = 100_000.0
DEFAULT_COMMISSION   = 0.0003
DEFAULT_STAMP_DUTY   = 0.001


# ── 账户 CRUD ──────────────────────────────────────────────────────────────────

def load_account() -> Dict:
    """读取账户状态，若文件不存在则新建默认账户。"""
    if not _ACCOUNT_PATH.exists():
        acc = _new_account(DEFAULT_INITIAL_CASH)
        save_account(acc)
        return acc
    with open(_ACCOUNT_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_account(account: Dict) -> None:
    """持久化账户状态。"""
    _ACCOUNT_PATH.parent.mkdir(parents=True, exist_ok=True)
    account["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(_ACCOUNT_PATH, "w", encoding="utf-8") as f:
        json.dump(account, f, ensure_ascii=False, indent=2)


def reset_account(initial_cash: float = DEFAULT_INITIAL_CASH) -> Dict:
    """清空账户，重新设置初始资金。"""
    acc = _new_account(initial_cash)
    save_account(acc)
    return acc


def _new_account(initial_cash: float) -> Dict:
    today = datetime.today().strftime("%Y-%m-%d")
    return {
        "initial_cash": initial_cash,
        "cash":         initial_cash,
        "positions":    [],
        "trades":       [],
        "nav_history":  [{"date": today, "nav": 1.0, "total": initial_cash}],
        "created_at":   today,
        "updated_at":   today,
    }


# ── 资产计算 ───────────────────────────────────────────────────────────────────

def get_total_assets(account: Dict) -> float:
    """总资产 = 现金 + 持仓市值（使用 current_price，无则用 cost）。"""
    position_value = sum(
        p["shares"] * p.get("current_price", p["cost"])
        for p in account["positions"]
    )
    return account["cash"] + position_value


def get_position_value(account: Dict) -> float:
    return sum(
        p["shares"] * p.get("current_price", p["cost"])
        for p in account["positions"]
    )


def get_today_pnl(account: Dict) -> float:
    """今日盈亏（以昨收近似：change_pct × 持仓市值）。"""
    pnl = 0.0
    for p in account["positions"]:
        price = p.get("current_price", p["cost"])
        chg   = p.get("change_pct", 0.0)
        pnl  += price * p["shares"] * chg / 100.0
    return pnl


# ── 行情刷新 ───────────────────────────────────────────────────────────────────

def refresh_prices(account: Dict) -> Dict:
    """
    调用腾讯实时行情接口更新所有持仓的现价和涨跌幅。
    网络失败时保留原有价格，不抛出异常。
    """
    if not account["positions"]:
        return account

    from core.data_provider import fetch_realtime

    codes  = [p["code"] for p in account["positions"]]
    quotes = {}
    try:
        quotes = fetch_realtime(codes)
    except Exception:
        pass

    for pos in account["positions"]:
        code = pos["code"]
        q = quotes.get(code) or quotes.get(f"sh{code}") or quotes.get(f"sz{code}")
        if q:
            pos["current_price"] = q["price"]
            pos["change_pct"]    = q.get("change_pct", 0.0)
            if not pos.get("name") or pos["name"] == code:
                pos["name"] = q.get("name", code)

    return account


# ── 交易执行 ───────────────────────────────────────────────────────────────────

def execute_buy(
    account:    Dict,
    code:       str,
    name:       str,
    price:      float,
    shares:     int,
    commission: float = DEFAULT_COMMISSION,
    source:     str   = "手动",
) -> Dict:
    """执行买入，扣除佣金，更新持仓和现金。"""
    if shares <= 0:
        raise ValueError("买入数量必须大于 0")
    if shares % 100 != 0:
        raise ValueError("A股最小交易单位为 100 股（1手）")

    fee   = round(price * shares * commission, 2)
    total = price * shares + fee

    if total > account["cash"]:
        raise ValueError(
            f"资金不足：需要 {total:,.2f} 元，可用 {account['cash']:,.2f} 元"
        )

    account["cash"] -= total

    # 更新或新建持仓
    existing = next((p for p in account["positions"] if p["code"] == code), None)
    if existing:
        total_shares = existing["shares"] + shares
        # 加权平均成本
        existing["cost"] = (
            existing["cost"] * existing["shares"] + price * shares
        ) / total_shares
        existing["shares"]        = total_shares
        existing["current_price"] = price
    else:
        account["positions"].append({
            "code":          code,
            "name":          name,
            "shares":        shares,
            "cost":          price,
            "current_price": price,
            "change_pct":    0.0,
            "buy_date":      datetime.today().strftime("%Y-%m-%d"),
        })

    account["trades"].append({
        "time":   datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "code":   code,
        "name":   name,
        "action": "买入",
        "price":  price,
        "shares": shares,
        "fee":    fee,
        "amount": round(total, 2),
        "status": "已成交",
        "source": source,
    })

    _record_nav(account)
    return account


def execute_sell(
    account:     Dict,
    code:        str,
    price:       float,
    shares:      int,
    commission:  float = DEFAULT_COMMISSION,
    stamp_duty:  float = DEFAULT_STAMP_DUTY,
    source:      str   = "手动",
) -> Dict:
    """执行卖出（含 T+1 检查），释放资金，更新持仓。"""
    pos = next((p for p in account["positions"] if p["code"] == code), None)
    if pos is None:
        raise ValueError(f"未持有 {code}")
    if pos["shares"] < shares:
        raise ValueError(
            f"持仓不足：持有 {pos['shares']} 股，尝试卖出 {shares} 股"
        )

    # T+1 约束
    today = datetime.today().strftime("%Y-%m-%d")
    if pos.get("buy_date") == today:
        raise ValueError("T+1 限制：当日买入的股票不能当日卖出")

    fee      = round(price * shares * (commission + stamp_duty), 2)
    proceeds = price * shares - fee
    account["cash"] += proceeds

    pos["shares"] -= shares
    if pos["shares"] == 0:
        account["positions"].remove(pos)

    account["trades"].append({
        "time":   datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "code":   code,
        "name":   pos["name"],
        "action": "卖出",
        "price":  price,
        "shares": shares,
        "fee":    fee,
        "amount": round(proceeds, 2),
        "status": "已成交",
        "source": source,
    })

    _record_nav(account)
    return account


# ── 净值记录 ───────────────────────────────────────────────────────────────────

def _record_nav(account: Dict) -> None:
    """将当前净值写入 nav_history（当天重复则覆盖）。"""
    today = datetime.today().strftime("%Y-%m-%d")
    total = get_total_assets(account)
    nav   = round(total / account["initial_cash"], 6)

    existing = next(
        (n for n in account["nav_history"] if n["date"] == today), None
    )
    if existing:
        existing["nav"]   = nav
        existing["total"] = round(total, 2)
    else:
        account["nav_history"].append(
            {"date": today, "nav": nav, "total": round(total, 2)}
        )


# ── 策略信号生成 ───────────────────────────────────────────────────────────────

def get_supertrend_signal_today(
    code:       str,
    period:     int   = 14,
    multiplier: float = 3.0,
) -> Dict:
    """
    获取单只股票今日 SuperTrend 信号。
    返回: {"code", "name", "action", "signal": 0|1, "time"}
    """
    from core.data_provider import fetch_history_em, get_stock_names_bulk
    from strategies.strategy_supertrend_ashare import supertrend_signal

    end   = datetime.today().strftime("%Y%m%d")
    start = (datetime.today() - timedelta(days=150)).strftime("%Y%m%d")

    try:
        df = fetch_history_em(code, start=start, end=end)
    except Exception as e:
        return {
            "code": code, "name": code,
            "action": f"数据获取失败: {e}", "signal": -1,
            "time": datetime.now().strftime("%H:%M:%S"),
        }

    if df.empty or len(df) < period + 5:
        return {
            "code": code, "name": code,
            "action": "历史数据不足", "signal": -1,
            "time": datetime.now().strftime("%H:%M:%S"),
        }

    sig            = supertrend_signal(df, period=period, multiplier=multiplier)
    today_signal   = int(sig.iloc[-1]) if len(sig) > 0 else 0
    yesterday_signal = int(sig.iloc[-2]) if len(sig) > 1 else today_signal

    if today_signal == 1 and yesterday_signal == 0:
        action = "买入信号"
    elif today_signal == 0 and yesterday_signal == 1:
        action = "卖出信号"
    elif today_signal == 1:
        action = "持有"
    else:
        action = "空仓"

    names = get_stock_names_bulk([code])
    return {
        "code":   code,
        "name":   names.get(code, code),
        "action": action,
        "signal": today_signal,
        "price":  round(float(df["close"].iloc[-1]), 2),
        "time":   datetime.now().strftime("%H:%M:%S"),
    }


def get_15x_signals_today() -> List[Dict]:
    """
    运行小市值 ROE 选股，返回今日推荐股票列表。
    返回: [{"code", "name", "roe", "roa", "market_cap", "pe", "pb"}, ...]
    """
    from strategies.strategy_15x import select_stocks
    try:
        return select_stocks(apply_blacklist=True)
    except Exception as e:
        return [{"code": "ERROR", "name": str(e)}]
