"""
模拟交易页面 — 实盘观察与模拟账户管理
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from config import STRATEGY_REGISTRY, DEFAULT_INITIAL_CASH
from web.charts import plot_nav_history
from web import paper_engine as pe


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _normalize_code(raw: str) -> str:
    return raw.strip().split(".")[0].zfill(6)


def _fmt_pnl(val: float) -> str:
    return f"+{val:,.2f}" if val >= 0 else f"{val:,.2f}"


# ── 账户概览 ──────────────────────────────────────────────────────────────────

def _show_account_summary(account: dict) -> None:
    total       = pe.get_total_assets(account)
    cash        = account["cash"]
    pos_val     = pe.get_position_value(account)
    today_pnl   = pe.get_today_pnl(account)
    total_pnl   = total - account["initial_cash"]
    total_ret   = total_pnl / account["initial_cash"] * 100

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("总资产（元）",   f"{total:,.0f}")
    c2.metric("可用资金（元）", f"{cash:,.0f}")
    c3.metric("持仓市值（元）", f"{pos_val:,.0f}")
    c4.metric("今日盈亏（元）", _fmt_pnl(today_pnl))
    c5.metric("总收益率",       f"{total_ret:+.2f}%")


# ── 当前持仓表 ─────────────────────────────────────────────────────────────────

def _show_positions(account: dict) -> None:
    st.markdown("#### 当前持仓")
    positions = account.get("positions", [])
    if not positions:
        st.info("暂无持仓")
        return

    rows = []
    for p in positions:
        price = p.get("current_price", p["cost"])
        cost  = p["cost"]
        pnl   = (price - cost) * p["shares"]
        pnl_pct = (price / cost - 1) * 100 if cost else 0
        rows.append({
            "代码":    p["code"],
            "名称":    p.get("name", p["code"]),
            "持仓(股)": p["shares"],
            "成本(元)": round(cost, 2),
            "现价(元)": round(price, 2),
            "涨跌幅":   f"{p.get('change_pct', 0):+.2f}%",
            "持仓盈亏": _fmt_pnl(round(pnl, 2)),
            "收益率":   f"{pnl_pct:+.2f}%",
        })

    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


# ── 信号展示 ───────────────────────────────────────────────────────────────────

def _show_signals(signals: list) -> None:
    st.markdown("#### 今日信号")
    if not signals:
        st.info("尚未生成信号，点击「获取信号」")
        return

    for s in signals:
        action = s.get("action", "")
        code   = s.get("code", "")
        name   = s.get("name", code)
        time_  = s.get("time", "")
        price  = s.get("price")

        icon = "🔴" if "买入" in action else ("🟢" if "卖出" in action else "⚪")
        price_str = f"  ¥{price:.2f}" if price else ""
        st.markdown(f"{icon} `{time_}`  **{code}** {name}  — {action}{price_str}")


# ── 交易记录 ───────────────────────────────────────────────────────────────────

def _show_trades(account: dict) -> None:
    st.markdown("#### 模拟交易记录")
    trades = account.get("trades", [])
    if not trades:
        st.info("暂无交易记录")
        return

    display = list(reversed(trades))  # 最新在前
    rows = []
    for t in display:
        rows.append({
            "时间":   t.get("time", ""),
            "代码":   t.get("code", ""),
            "名称":   t.get("name", ""),
            "动作":   t.get("action", ""),
            "价格":   t.get("price", 0),
            "数量":   t.get("shares", 0),
            "手续费": t.get("fee", 0),
            "金额":   t.get("amount", 0),
            "状态":   t.get("status", ""),
            "来源":   t.get("source", ""),
        })
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


# ── 手动交易表单 ──────────────────────────────────────────────────────────────

def _manual_trade_form(account: dict) -> dict:
    """展示手动买入/卖出表单，返回（可能更新的）account。"""
    with st.expander("手动买入 / 卖出", expanded=False):
        tab_buy, tab_sell = st.tabs(["买入", "卖出"])

        with tab_buy:
            b_code  = st.text_input("股票代码", key="pt_buy_code", placeholder="600519")
            b_name  = st.text_input("股票名称", key="pt_buy_name", placeholder="（选填）")
            b_price = st.number_input("买入价格", min_value=0.01, value=10.0, step=0.01, key="pt_buy_price")
            b_lots  = st.number_input("买入手数（每手100股）", min_value=1, max_value=1000, value=1, key="pt_buy_lots")
            b_btn   = st.button("确认买入", type="primary", key="pt_buy_btn")

            if b_btn:
                code   = _normalize_code(b_code)
                shares = int(b_lots) * 100
                try:
                    account = pe.execute_buy(
                        account, code, b_name or code,
                        float(b_price), shares, source="手动",
                    )
                    pe.save_account(account)
                    st.success(f"买入成功：{code}  {shares}股  @ {b_price:.2f}")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))

        with tab_sell:
            positions = account.get("positions", [])
            if not positions:
                st.info("暂无持仓可卖出")
            else:
                s_options = {
                    f"{p['code']} {p.get('name','')} ({p['shares']}股)": p["code"]
                    for p in positions
                }
                s_label  = st.selectbox("选择持仓", list(s_options.keys()), key="pt_sell_sel")
                s_code   = s_options[s_label]
                pos_info = next((p for p in positions if p["code"] == s_code), {})
                s_price  = st.number_input(
                    "卖出价格", min_value=0.01,
                    value=float(pos_info.get("current_price", pos_info.get("cost", 10.0))),
                    step=0.01, key="pt_sell_price",
                )
                max_lots = pos_info.get("shares", 100) // 100
                s_lots   = st.number_input(
                    "卖出手数", min_value=1, max_value=max(max_lots, 1),
                    value=max_lots, key="pt_sell_lots",
                )
                s_btn = st.button("确认卖出", type="primary", key="pt_sell_btn")

                if s_btn:
                    shares = int(s_lots) * 100
                    try:
                        account = pe.execute_sell(
                            account, s_code, float(s_price), shares, source="手动",
                        )
                        pe.save_account(account)
                        st.success(f"卖出成功：{s_code}  {shares}股  @ {s_price:.2f}")
                        st.rerun()
                    except ValueError as e:
                        st.error(str(e))

    return account


# ── 主渲染函数 ────────────────────────────────────────────────────────────────

def render() -> None:
    st.markdown("## A股模拟交易")
    st.caption("用实时/准实时行情运行策略，观察信号与持仓变化")

    # 初始化 session 中的 signals 列表
    if "pt_signals" not in st.session_state:
        st.session_state["pt_signals"] = []

    # ── 加载账户 ──
    account = pe.load_account()

    # ── 左侧控制区 ──
    left, right = st.columns([1, 3])

    with left:
        st.markdown("### 控制台")

        strategy_label = st.selectbox(
            "策略",
            list(STRATEGY_REGISTRY.keys()),
            key="pt_strategy",
        )
        strategy_id = STRATEGY_REGISTRY[strategy_label]

        if strategy_id == "supertrend":
            # 读取从单只回测跳转过来的预填代码
            _prefill = st.session_state.pop("pt_prefill_code", "")
            _default_code = _prefill if _prefill else st.session_state.get("pt_code", "600519")
            if _prefill:
                st.success(f"已从回测跳转：{_prefill}")
            pt_code = st.text_input("股票代码", value=_default_code, key="pt_code")
            from config import STRATEGY_SUPERTREND
            period     = st.number_input("ATR 周期",  value=STRATEGY_SUPERTREND["period"],     min_value=5,   max_value=50,  key="pt_period")
            multiplier = st.number_input("ATR 乘数",  value=STRATEGY_SUPERTREND["multiplier"], min_value=0.5, max_value=10.0, step=0.5, key="pt_mult")
        else:
            pt_code    = None
            period     = 14
            multiplier = 3.0

        st.markdown("---")
        pos_limit  = st.slider("单票仓位上限（%）", min_value=5, max_value=100, value=20, step=5, key="pt_pos_limit")
        take_profit = st.number_input("止盈（%）", min_value=1, max_value=200, value=10, key="pt_tp")
        stop_loss   = st.number_input("止损（%）", min_value=1, max_value=100, value=5,  key="pt_sl")

        st.markdown("---")
        col_a, col_b = st.columns(2)
        signal_btn  = col_a.button("获取信号", type="primary", width="stretch", key="pt_signal")
        refresh_btn = col_b.button("刷新行情", width="stretch", key="pt_refresh")

        st.markdown("---")
        with st.expander("账户设置", expanded=False):
            new_cash = st.number_input(
                "重置初始资金", min_value=10_000, max_value=10_000_000,
                value=int(account["initial_cash"]), step=10_000, key="pt_init_cash",
            )
            if st.button("重置账户", type="secondary", key="pt_reset"):
                if st.session_state.get("pt_reset_confirm"):
                    account = pe.reset_account(float(new_cash))
                    st.session_state["pt_signals"] = []
                    st.session_state["pt_reset_confirm"] = False
                    st.success("账户已重置")
                    st.rerun()
                else:
                    st.session_state["pt_reset_confirm"] = True
                    st.warning("再次点击「重置账户」确认操作（不可撤销）")

    # ── 处理按钮事件 ──
    if refresh_btn:
        with st.spinner("刷新行情中..."):
            account = pe.refresh_prices(account)
            pe.save_account(account)
        st.toast("行情已刷新")

    if signal_btn:
        with st.spinner("生成策略信号..."):
            if strategy_id == "supertrend" and pt_code:
                code = _normalize_code(pt_code)
                sig  = pe.get_supertrend_signal_today(code, int(period), float(multiplier))
                # 检查止盈止损
                pos = next((p for p in account["positions"] if p["code"] == code), None)
                if pos:
                    pnl_pct = (pos.get("current_price", pos["cost"]) / pos["cost"] - 1) * 100
                    if pnl_pct >= take_profit:
                        sig["action"] = f"止盈信号（+{pnl_pct:.1f}%）"
                    elif pnl_pct <= -stop_loss:
                        sig["action"] = f"止损信号（{pnl_pct:.1f}%）"
                st.session_state["pt_signals"] = [sig]
            else:  # 15x / rotation
                signals_raw = pe.get_15x_signals_today()
                st.session_state["pt_signals"] = [
                    {
                        "code":   s.get("code", ""),
                        "name":   s.get("name", ""),
                        "action": "选股推荐",
                        "price":  None,
                        "time":   __import__("datetime").datetime.now().strftime("%H:%M:%S"),
                        "roe":    s.get("roe"),
                        "market_cap": s.get("market_cap"),
                    }
                    for s in signals_raw
                ]

    # ── 右侧账户面板 ──
    with right:
        _show_account_summary(account)
        st.divider()

        col_pos, col_sig = st.columns(2)

        with col_pos:
            _show_positions(account)

        with col_sig:
            signals = st.session_state.get("pt_signals", [])
            _show_signals(signals)

            # 15x 策略显示选股详情表
            if strategy_id != "supertrend" and signals and signals[0].get("roe") is not None:
                df_sig = pd.DataFrame([
                    {
                        "代码":   s["code"],
                        "名称":   s.get("name", ""),
                        "ROE%":   s.get("roe"),
                        "市值(亿)": s.get("market_cap"),
                    }
                    for s in signals
                ])
                st.dataframe(df_sig, width="stretch", hide_index=True)

        st.divider()

        # 净值走势
        st.plotly_chart(
            plot_nav_history(account.get("nav_history", [])),
            width="stretch",
        )

    st.divider()

    # ── 手动交易 ──
    account = _manual_trade_form(account)

    # ── 交易记录 ──
    _show_trades(account)
