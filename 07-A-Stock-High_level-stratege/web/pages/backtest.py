"""
web/pages/backtest.py — 回测页面

Step 1: 组合轮动回测 (15x / rotation)
Step 2: 单只股票回测 (SuperTrend)

UX 流程:
  Step 1 跑完 → 点击股票代码 → 自动跳转 Step 2（代码预填、自动运行）
  Step 2 跑完 → 点击「开始模拟此股」 → 跳转模拟交易页
"""
from __future__ import annotations

import streamlit as st
import pandas as pd
import numpy as np
from datetime import date, timedelta
from typing import Dict, List, Optional

from web.charts import plot_kline_signals, plot_equity_curve, plot_drawdown

from config import (
    STRATEGY_SUPERTREND, STRATEGY_15X,
    DEFAULT_INITIAL_CASH, DEFAULT_COMMISSION, DEFAULT_STAMP_DUTY,
)

# 组合轮动策略选项（不含 SuperTrend）
_MONTHLY_STRATEGIES = {
    "小市值ROE（15x策略）": "15x",
    "大盘轮动":             "rotation",
}


# ─────────────────────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────────────────────

def _to_em_date(d: date) -> str:
    return d.strftime("%Y%m%d")


def _normalize_code(raw: str) -> str:
    s = raw.strip()
    return s.zfill(6)[:6] if s.isdigit() else s[:6] if s else ""


def _fmt_pct(v) -> str:
    if v is None:
        return "N/A"
    return f"{float(v):+.2f}%"


def _compute_profit_loss_ratio(result_df: pd.DataFrame) -> float:
    """从 SuperTrend result_df 计算盈亏比。"""
    if "pos" not in result_df.columns and "position" not in result_df.columns:
        return 0.0
    pos_col = "pos" if "pos" in result_df.columns else "position"
    pos = result_df[pos_col]
    entry_price = None
    pnls = []
    for i in range(len(pos)):
        if pos.iloc[i] == 1 and (i == 0 or pos.iloc[i - 1] == 0):
            entry_price = result_df["close"].iloc[i] if "close" in result_df else None
        if pos.iloc[i] == 0 and i > 0 and pos.iloc[i - 1] == 1 and entry_price:
            exit_price = result_df["close"].iloc[i] if "close" in result_df else None
            if exit_price:
                pnls.append(exit_price - entry_price)
            entry_price = None
    if not pnls:
        return 0.0
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    if not wins or not losses:
        return 0.0
    return round(float(np.mean(wins)) / float(abs(np.mean(losses))), 2)


# ─────────────────────────────────────────────────────────────────────────────
# 交易记录提取（SuperTrend result_df → trades list）
# ─────────────────────────────────────────────────────────────────────────────

def _extract_trades_supertrend(
    result_df: pd.DataFrame,
    initial_cash: float,
    commission: float,
    stamp_duty: float,
) -> List[Dict]:
    trades = []
    if result_df.empty:
        return trades

    pos_col = "pos" if "pos" in result_df.columns else "position" if "position" in result_df.columns else None
    if pos_col is None:
        return trades

    pos   = result_df[pos_col]
    close = result_df["close"] if "close" in result_df.columns else None
    if close is None:
        return trades

    cash   = float(initial_cash)
    shares = 0
    buy_price = 0.0

    for i in range(len(pos)):
        d     = pos.index[i]
        price = float(close.iloc[i])
        p     = int(pos.iloc[i])
        p_prev = int(pos.iloc[i - 1]) if i > 0 else 0

        if p == 1 and p_prev == 0:  # 买入
            fee = price * commission
            lots = int(cash / ((price + fee) * 100))
            if lots <= 0:
                continue
            shares = lots * 100
            cost   = shares * (price + fee)
            cash  -= cost
            buy_price = price
            trades.append({
                "日期": d.strftime("%Y-%m-%d"),
                "动作": "买入",
                "价格": round(price, 3),
                "股数": shares,
                "金额": round(cost, 2),
                "手续费": round(shares * fee, 2),
                "收益": "--",
            })

        elif p == 0 and p_prev == 1 and shares > 0:  # 卖出
            fee  = price * (commission + stamp_duty)
            gain = shares * (price - buy_price - fee)
            cash += shares * (price - fee)
            trades.append({
                "日期": d.strftime("%Y-%m-%d"),
                "动作": "卖出",
                "价格": round(price, 3),
                "股数": shares,
                "金额": round(shares * (price - fee), 2),
                "手续费": round(shares * fee, 2),
                "收益": f"{gain:+.2f}",
            })
            shares = 0

    return trades


# ─────────────────────────────────────────────────────────────────────────────
# 指标展示
# ─────────────────────────────────────────────────────────────────────────────

def _show_metrics(m: Dict, cols: int = 6) -> None:
    """展示指标卡片，m 中有哪些 key 就展示哪些。"""
    schema = [
        ("total_return",       "策略累计收益", "%"),
        ("bh_return",          "买入持有收益", "%"),
        ("annual_return",      "年化收益",    "%"),
        ("max_drawdown",       "最大回撤",    "%"),
        ("sharpe",             "夏普比率",    ""),
        ("win_rate",           "胜率",        "%"),
        ("profit_loss_ratio",  "盈亏比",      "x"),
    ]
    items = [(label, unit, m.get(key)) for key, label, unit in schema if key in m]
    c = st.columns(len(items))
    for i, (label, unit, val) in enumerate(items):
        if val is None:
            c[i].metric(label, "N/A")
            continue
        if unit == "%":
            disp = f"{float(val):+.2f}%"
        elif unit == "x":
            disp = f"{float(val):.2f}x"
        else:
            disp = f"{float(val):.3f}"
        c[i].metric(label, disp)


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 — SuperTrend 单只股票回测
# ─────────────────────────────────────────────────────────────────────────────

def _run_supertrend_and_store(
    code: str, start_em: str, end_em: str,
    initial_cash: float, commission: float, stamp_duty: float,
    period: int, multiplier: float, use_wilder: bool,
) -> None:
    from core.data_provider import fetch_history_em, fetch_index_history, get_stock_names_bulk
    from strategies.strategy_supertrend_ashare import (
        supertrend_signal, compute_returns_ashare, metrics as st_metrics,
    )

    with st.spinner("获取历史K线..."):
        try:
            df = fetch_history_em(code, start=start_em, end=end_em)
        except Exception as e:
            st.error(f"数据获取失败：{e}")
            return

    if df.empty:
        st.error("未获取到数据，请检查代码和日期。")
        return

    names      = get_stock_names_bulk([code])
    stock_name = names.get(code, code)

    with st.spinner("计算 SuperTrend 信号..."):
        signal    = supertrend_signal(df, period=period, multiplier=multiplier, use_wilder=use_wilder)
        result_df = compute_returns_ashare(df["close"], signal, commission, stamp_duty)

    m = st_metrics(result_df)
    m["profit_loss_ratio"] = _compute_profit_loss_ratio(result_df)

    # 买入持有收益
    if "bh_equity" in result_df.columns and len(result_df) > 1:
        bh_ret = float((result_df["bh_equity"].iloc[-1] / result_df["bh_equity"].iloc[0] - 1) * 100)
        m["bh_return"] = round(bh_ret, 2)

    # 基准
    bench_series = None
    with st.spinner("获取沪深300基准..."):
        try:
            bench_df     = fetch_index_history("000300", start=start_em, end=end_em)
            bench_series = bench_df["close"] if not bench_df.empty else None
        except Exception:
            pass

    # 组合持有期曲线（从 Step 1 跳转时计算）
    portfolio_held_series = None
    if st.session_state.get("bt_held_for_code") == code:
        held_periods = st.session_state.pop("bt_held_periods", [])
        portfolio_trades = st.session_state.pop("bt_portfolio_trades", None)
        st.session_state.pop("bt_held_for_code", None)
        if held_periods and not df.empty:
            _daily_ret  = df["close"].pct_change().fillna(0.0)
            _restricted = pd.Series(0.0, index=df.index)
            for _buy_str, _sell_str in held_periods:
                _mask = (df.index > pd.Timestamp(_buy_str)) & (df.index <= pd.Timestamp(_sell_str))
                _restricted[_mask] = _daily_ret[_mask]
            portfolio_held_series = (1 + _restricted).cumprod()
    else:
        portfolio_trades = None

    trades = _extract_trades_supertrend(result_df, initial_cash, commission, stamp_duty)

    # Merge result_df close into trades df (needed for close prices in result)
    # Store result_df with a close column for kline display
    if "close" not in result_df.columns:
        result_df = result_df.copy()
        result_df["close"] = df["close"]

    st.session_state["supertrend_result"] = {
        "df":                    df,
        "result_df":             result_df,
        "m":                     m,
        "equity_series":         result_df["equity"],
        "bh_series":             result_df.get("bh_equity"),
        "bench_series":          bench_series,
        "portfolio_held_series": portfolio_held_series,
        "portfolio_trades":      portfolio_trades,
        "trades":                trades,
        "code":                  code,
        "stock_name":            stock_name,
        "start_em":              start_em,
        "end_em":                end_em,
        "period":                period,
        "multiplier":            multiplier,
        "initial_cash":          initial_cash,
    }


def _display_supertrend_result() -> None:
    if "supertrend_result" not in st.session_state:
        return
    s            = st.session_state["supertrend_result"]
    code         = s["code"]
    stock_name   = s["stock_name"]
    start_em     = s["start_em"]
    end_em       = s["end_em"]
    initial_cash = s["initial_cash"]

    st.subheader(
        f"SuperTrend ({s['period']}, {s['multiplier']})  ·  "
        f"{stock_name}（{code}）  ·  "
        f"{start_em[:4]}-{start_em[4:6]}-{start_em[6:]} ~ "
        f"{end_em[:4]}-{end_em[4:6]}-{end_em[6:]}"
    )
    _show_metrics(s["m"])
    st.divider()

    df_full     = s["df"]
    trades_full = s["trades"]

    # 若从组合跳转，优先展示组合的真实买卖点
    portfolio_trades = s.get("portfolio_trades")
    kline_trades     = portfolio_trades if portfolio_trades else trades_full
    kline_source     = "组合实际买卖" if portfolio_trades else "SuperTrend"

    st.caption(
        f"K线说明：  "
        f"🔴 红色=上涨  🟢 绿色=下跌  "
        f"🟡 金黄▲=买入（{kline_source}）  "
        f"🔵 青色▼=卖出（{kline_source}）"
    )
    st.plotly_chart(
        plot_kline_signals(df_full, kline_trades,
                           title=f"{stock_name} K线 + 买卖信号（{kline_source}）"),
        width="stretch",
    )

    has_ph = s.get("portfolio_held_series") is not None
    st.plotly_chart(
        plot_equity_curve(
            s["equity_series"], s.get("bh_series"), s["bench_series"],
            initial_cash=initial_cash,
            portfolio_held=s.get("portfolio_held_series"),
        ),
        width="stretch",
    )

    st.caption("回撤曲线说明：红色填充 = 策略回撤深度")
    st.plotly_chart(plot_drawdown(s["equity_series"]), width="stretch")

    st.subheader("交易记录")
    if trades_full:
        st.dataframe(pd.DataFrame(trades_full), width="stretch", hide_index=True)
    else:
        st.info("回测期间无交易信号。")

    st.divider()
    st.markdown("**下一步**")
    if st.button(f"→ 开始模拟此股  {code} {stock_name}", type="primary", key="bt_goto_paper"):
        st.session_state["pt_prefill_code"] = code
        st.session_state["nav_page"]        = "模拟交易"
        st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — 组合轮动回测
# ─────────────────────────────────────────────────────────────────────────────

def _run_monthly_and_store(
    strategy_id: str, start_em: str, end_em: str,
    initial_cash: float, commission: float, stock_num: int,
) -> None:
    from core.backtest_engine import run_backtest
    from core.data_provider import get_stock_names_bulk

    progress_bar = st.progress(0, text="初始化回测...")

    def on_progress(pct: int, msg: str) -> None:
        progress_bar.progress(min(int(pct), 100), text=msg)

    try:
        result = run_backtest(
            strategy_id  = strategy_id,
            start_date   = start_em,
            end_date     = end_em,
            initial_cash = initial_cash,
            commission   = commission,
            stock_num    = stock_num,
            progress_cb  = on_progress,
        )
    except Exception as e:
        progress_bar.empty()
        st.error(f"回测失败：{e}")
        return

    progress_bar.empty()

    if "error" in result:
        st.error(result["error"])
        return

    m = result.get("metrics", {})

    # 月度盈亏比
    equity_records = result.get("equity", [])
    if equity_records:
        eq_df  = pd.DataFrame(equity_records)
        eq_df["date"] = pd.to_datetime(eq_df["date"])
        eq_s   = eq_df.set_index("date")["value"]
        monthly = eq_s.resample("ME").last().pct_change().dropna()
        wins    = monthly[monthly > 0]
        losses  = monthly[monthly < 0]
        if len(wins) > 0 and len(losses) > 0:
            m["profit_loss_ratio"] = round(float(wins.mean()) / float(abs(losses.mean())), 2)

    held_codes = result.get("held_codes", [])
    names_map  = get_stock_names_bulk(held_codes) if held_codes else {}

    st.session_state["portfolio_result"] = {
        "result":        result,
        "metrics":       m,
        "strategy_id":   strategy_id,
        "strategy_name": {
            "15x":      "小市值ROE（15x策略）",
            "rotation": "大盘轮动",
        }.get(strategy_id, strategy_id),
        "start_em":      start_em,
        "end_em":        end_em,
        "initial_cash":  initial_cash,
        "held_codes":    held_codes,
        "names_map":     names_map,
    }


def _display_monthly_result() -> None:
    if "portfolio_result" not in st.session_state:
        return
    s            = st.session_state["portfolio_result"]
    result       = s["result"]
    m            = s["metrics"]
    start_em     = s["start_em"]
    end_em       = s["end_em"]
    initial_cash = s["initial_cash"]
    held_codes   = s["held_codes"]
    names_map    = s["names_map"]
    trades_all   = result.get("trades", [])

    st.subheader(
        f"策略：{s['strategy_name']}  ·  "
        f"{start_em[:4]}-{start_em[4:6]}-{start_em[6:]} ~ "
        f"{end_em[:4]}-{end_em[4:6]}-{end_em[6:]}"
    )
    _show_metrics(m)

    if result.get("disclaimer"):
        st.caption(f"⚠️ {result['disclaimer']}")
    st.divider()

    equity_records = result.get("equity", [])
    bench_records  = result.get("benchmark", [])

    if equity_records:
        eq_df = pd.DataFrame(equity_records)
        eq_df["date"] = pd.to_datetime(eq_df["date"])
        eq_series = eq_df.set_index("date")["value"]

        bench_series = None
        if bench_records:
            bm_df = pd.DataFrame(bench_records)
            bm_df["date"] = pd.to_datetime(bm_df["date"])
            bench_series = bm_df.set_index("date")["value"]

        st.caption("蓝色=策略  ··· 紫色=沪深300基准")
        st.plotly_chart(
            plot_equity_curve(eq_series, benchmark=bench_series, initial_cash=initial_cash),
            width="stretch",
        )
        st.caption("红色填充 = 组合回撤深度")
        st.plotly_chart(plot_drawdown(eq_series), width="stretch")

    # ── 历史持仓成分 ──
    if held_codes:
        # 最终持仓（回测结束时仍持有的股票）
        net: dict = {}
        for t in trades_all:
            c  = t.get("code", "")
            sh = t.get("shares", 0)
            if t.get("action") == "买入":
                net[c] = net.get(c, 0) + sh
            else:
                net[c] = net.get(c, 0) - sh
        final_pos = {c: sh for c, sh in net.items() if sh > 0}
        if final_pos:
            st.subheader(f"最终持仓（{len(final_pos)} 只）")
            st.dataframe(
                pd.DataFrame([
                    {"代码": c, "名称": names_map.get(c, c), "持仓股数": sh}
                    for c, sh in final_pos.items()
                ]),
                width="stretch", hide_index=True,
            )
            st.divider()

        st.subheader(f"历史持仓成分（共 {len(held_codes)} 只）")
        st.caption("回测期间曾被选入的全部股票 · 点击代码跳转到单只回测")

        comp_rows = [
            {"代码": c, "名称": names_map.get(c, c)}
            for c in held_codes
        ]
        st.dataframe(pd.DataFrame(comp_rows), width="stretch", hide_index=True)

        st.markdown("**下一步：分析单只股票**")
        _COLS_PER_ROW = 6
        for row_start in range(0, len(held_codes), _COLS_PER_ROW):
            row_codes = held_codes[row_start:row_start + _COLS_PER_ROW]
            jump_cols = st.columns(len(row_codes))
            for i, code in enumerate(row_codes):
                name = names_map.get(code, code)
                if jump_cols[i].button(f"{code}\n{name}", key=f"bt_jump_{code}"):
                    # 提取该股票的持仓区间 + 交易记录
                    _code_trades = sorted(
                        [t for t in trades_all if t.get("code") == code],
                        key=lambda t: t.get("date", ""),
                    )
                    _held_periods: list = []
                    _buy_date = None
                    for _t in _code_trades:
                        if _t.get("action") == "买入" and _buy_date is None:
                            _buy_date = _t["date"]
                        elif _t.get("action") == "卖出" and _buy_date is not None:
                            _held_periods.append((_buy_date, _t["date"]))
                            _buy_date = None
                    if _buy_date is not None:
                        _held_periods.append((_buy_date, end_em))

                    st.session_state["bt_prefill_code"]     = code
                    st.session_state["bt_auto_run"]         = True
                    st.session_state["bt_active_tab_pending"] = "Step 2  单只股票回测"
                    st.session_state["bt_held_periods"]     = _held_periods
                    st.session_state["bt_held_for_code"]    = code
                    st.session_state["bt_portfolio_trades"] = _code_trades
                    st.rerun()

    # ── 交易记录 ──
    if trades_all:
        st.subheader(f"交易记录（共 {len(trades_all)} 笔）")
        st.dataframe(pd.DataFrame(trades_all), width="stretch", hide_index=True)
    else:
        st.info("无交易记录。")


# ─────────────────────────────────────────────────────────────────────────────
# 主渲染函数
# ─────────────────────────────────────────────────────────────────────────────

def render() -> None:
    st.markdown("## A股策略回测")
    st.caption(
        "推荐流程：**Step 1** 组合轮动回测选出股票池 → "
        "**Step 2** 对单只股票深入回测 → **Step 3** 开始模拟交易"
    )

    # ── Tab 导航（用 radio 实现程序化切换）──
    if "bt_active_tab" not in st.session_state:
        st.session_state["bt_active_tab"] = "Step 1  组合轮动回测"

    # 处理跳转请求（在 widget 创建前应用，避免 StreamlitAPIException）
    if "bt_active_tab_pending" in st.session_state:
        st.session_state["bt_active_tab"] = st.session_state.pop("bt_active_tab_pending")

    active_tab = st.radio(
        "步骤",
        ["Step 1  组合轮动回测", "Step 2  单只股票回测"],
        index=["Step 1  组合轮动回测", "Step 2  单只股票回测"].index(
            st.session_state["bt_active_tab"]
        ),
        horizontal=True,
        key="bt_active_tab",
        label_visibility="collapsed",
    )

    st.divider()

    today     = date.today()
    default_s = date(2024, 1, 1)

    # ══════════════════════════════════════════════════════════════════════════
    if active_tab == "Step 1  组合轮动回测":
    # ══════════════════════════════════════════════════════════════════════════
        left, right = st.columns([1, 3])
        with left:
            st.markdown("### 参数设置")
            strategy_label = st.selectbox(
                "策略", list(_MONTHLY_STRATEGIES.keys()), key="bt_m_strategy",
            )
            strategy_id = _MONTHLY_STRATEGIES[strategy_label]

            start_d = st.date_input("开始日期", value=default_s, key="bt_m_start")
            end_d   = st.date_input("结束日期", value=today, key="bt_m_end")

            initial_cash = st.number_input(
                "初始资金（元）", min_value=10_000, max_value=10_000_000,
                value=int(DEFAULT_INITIAL_CASH), step=10_000, key="bt_m_cash",
            )
            commission = st.number_input(
                "手续费率", min_value=0.0001, max_value=0.01,
                value=DEFAULT_COMMISSION, format="%.4f", key="bt_m_comm",
            )
            stock_num = st.number_input(
                "持仓数量", min_value=1, max_value=50,
                value=10 if strategy_id == "15x" else 3,
                key="bt_m_num",
            )

            st.markdown("---")
            run_btn   = st.button("▶ 开始回测", type="primary", width="stretch", key="bt_m_run")
            reset_btn = st.button("↺ 重置结果", width="stretch", key="bt_m_reset")
            if reset_btn:
                st.session_state.pop("portfolio_result", None)
                st.rerun()

        with right:
            if run_btn:
                if start_d >= end_d:
                    st.error("开始日期必须早于结束日期。")
                else:
                    _run_monthly_and_store(
                        strategy_id  = strategy_id,
                        start_em     = _to_em_date(start_d),
                        end_em       = _to_em_date(end_d),
                        initial_cash = float(initial_cash),
                        commission   = float(commission),
                        stock_num    = int(stock_num),
                    )
            _display_monthly_result()
            if "portfolio_result" not in st.session_state:
                st.info("在左侧设置参数，点击「开始回测」查看结果。")

    # ══════════════════════════════════════════════════════════════════════════
    else:  # Step 2  单只股票回测
    # ══════════════════════════════════════════════════════════════════════════
        left, right = st.columns([1, 3])
        with left:
            st.markdown("### 参数设置")

            _prefill = st.session_state.pop("bt_prefill_code", "")
            _default_code = _prefill if _prefill else st.session_state.get("bt_s_code_val", "600519")
            raw_code = st.text_input("股票代码", value=_default_code, key="bt_s_code")
            st.session_state["bt_s_code_val"] = raw_code
            code = _normalize_code(raw_code)

            start_d = st.date_input("开始日期", value=default_s, key="bt_s_start")
            end_d   = st.date_input("结束日期", value=today, key="bt_s_end")

            initial_cash = st.number_input(
                "初始资金（元）", min_value=10_000, max_value=10_000_000,
                value=int(DEFAULT_INITIAL_CASH), step=10_000, key="bt_s_cash",
            )
            commission = st.number_input(
                "手续费率", min_value=0.0001, max_value=0.01,
                value=DEFAULT_COMMISSION, format="%.4f", key="bt_s_comm",
            )
            stamp_duty = st.number_input(
                "印花税率（卖出）", min_value=0.0, max_value=0.01,
                value=DEFAULT_STAMP_DUTY, format="%.4f", key="bt_s_stamp",
            )

            st.markdown("---")
            st.markdown("**SuperTrend 参数**")
            period     = st.number_input("ATR 周期",   min_value=5, max_value=50,
                                          value=STRATEGY_SUPERTREND["period"],  key="bt_s_period")
            multiplier = st.number_input("ATR 乘数",   min_value=0.5, max_value=10.0,
                                          value=STRATEGY_SUPERTREND["multiplier"], step=0.5, key="bt_s_mult")
            use_wilder = st.checkbox("Wilder 平滑（推荐）", value=True, key="bt_s_wilder")

            st.markdown("---")
            run_btn   = st.button("▶ 开始回测", type="primary", width="stretch", key="bt_s_run")
            reset_btn = st.button("↺ 重置结果", width="stretch", key="bt_s_reset")
            if reset_btn:
                st.session_state.pop("supertrend_result", None)
                st.rerun()

        with right:
            auto_run = st.session_state.pop("bt_auto_run", False)
            if run_btn or auto_run:
                if not code or len(code) != 6:
                    st.error("请输入合法的 6 位股票代码。")
                elif start_d >= end_d:
                    st.error("开始日期必须早于结束日期。")
                else:
                    _run_supertrend_and_store(
                        code         = code,
                        start_em     = _to_em_date(start_d),
                        end_em       = _to_em_date(end_d),
                        initial_cash = float(initial_cash),
                        commission   = float(commission),
                        stamp_duty   = float(stamp_duty),
                        period       = int(period),
                        multiplier   = float(multiplier),
                        use_wilder   = bool(use_wilder),
                    )
            _display_supertrend_result()
            if "supertrend_result" not in st.session_state:
                st.info("在左侧设置参数，点击「开始回测」查看结果。")
