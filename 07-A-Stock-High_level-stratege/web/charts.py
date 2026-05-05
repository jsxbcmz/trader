"""
图表工具模块 — Plotly 图表封装

提供函数:
  plot_kline_signals   — K线图 + 买卖信号标注
  plot_equity_curve    — 收益曲线对比（策略 / 买入持有 / 基准）
  plot_drawdown        — 回撤曲线
  plot_nav_history     — 模拟交易净值走势
"""
from __future__ import annotations

from typing import List, Dict, Optional

import pandas as pd
import plotly.graph_objects as go

# A股配色：红涨绿跌
_RED    = "#ef5350"
_GREEN  = "#26a69a"
_BLUE   = "#2196F3"
_AMBER  = "#FF9800"
_PURPLE = "#9C27B0"
_TMPL   = "plotly_dark"

# 买卖信号专用色（与K线蜡烛区分）
_BUY_COLOR  = "#FFD700"   # 亮黄（金）—— 买入
_SELL_COLOR = "#00E5FF"   # 亮青     —— 卖出

_RANGE_BUTTONS = [
    dict(count=1,  label="1月", step="month", stepmode="backward"),
    dict(count=3,  label="3月", step="month", stepmode="backward"),
    dict(count=6,  label="6月", step="month", stepmode="backward"),
    dict(count=1,  label="1年", step="year",  stepmode="backward"),
    dict(count=2,  label="2年", step="year",  stepmode="backward"),
    dict(step="all", label="全部"),
]

def _rangeselector(activecolor: str = "#2196F3") -> dict:
    return dict(
        buttons=_RANGE_BUTTONS,
        bgcolor="#2d2d2d",
        activecolor=activecolor,
        font=dict(color="#ffffff", size=11),
        x=0.5,
        xanchor="center",
        y=1.0,
        yanchor="bottom",
    )


# ── K线 + 买卖信号 ─────────────────────────────────────────────────────────────

def plot_kline_signals(
    df: pd.DataFrame,
    trades: List[Dict],
    title: str = "K线 + 买卖信号",
) -> go.Figure:
    """
    参数
    ----
    df     : 含 open/high/low/close 列的日线 DataFrame（DatetimeIndex）
    trades : 交易列表，每条含 action/date/price（支持中英文 key）
    """
    def _v(t, *keys):
        for k in keys:
            if k in t:
                return t[k]
        return None

    fig = go.Figure()

    fig.add_trace(
        go.Candlestick(
            x=df.index,
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"],
            name="K线",
            increasing_line_color=_RED,
            decreasing_line_color=_GREEN,
            increasing_fillcolor=_RED,
            decreasing_fillcolor=_GREEN,
        )
    )

    buys = [t for t in trades if _v(t, "action", "动作") == "买入"]
    if buys:
        buy_dates  = [pd.Timestamp(_v(t, "date", "日期")) for t in buys]
        buy_prices = [float(_v(t, "price", "价格")) * 0.975 for t in buys]
        fig.add_trace(
            go.Scatter(
                x=buy_dates, y=buy_prices,
                mode="markers",
                marker=dict(symbol="triangle-up", size=16, color=_BUY_COLOR,
                            line=dict(color="#000000", width=1)),
                name="买入",
                hovertemplate="<b>买入</b><br>%{x|%Y-%m-%d}<br>价格: %{customdata:.2f}<extra></extra>",
                customdata=[float(_v(t, "price", "价格")) for t in buys],
            )
        )

    sells = [t for t in trades if _v(t, "action", "动作") == "卖出"]
    if sells:
        sell_dates  = [pd.Timestamp(_v(t, "date", "日期")) for t in sells]
        sell_prices = [float(_v(t, "price", "价格")) * 1.025 for t in sells]
        fig.add_trace(
            go.Scatter(
                x=sell_dates, y=sell_prices,
                mode="markers",
                marker=dict(symbol="triangle-down", size=16, color=_SELL_COLOR,
                            line=dict(color="#000000", width=1)),
                name="卖出",
                hovertemplate="<b>卖出</b><br>%{x|%Y-%m-%d}<br>价格: %{customdata:.2f}<extra></extra>",
                customdata=[float(_v(t, "price", "价格")) for t in sells],
            )
        )

    fig.update_layout(
        title=dict(text=title, x=0.5, xanchor="center"),
        xaxis=dict(
            title="日期",
            rangeselector=_rangeselector(),
            rangeslider=dict(visible=False),
            type="date",
        ),
        yaxis=dict(title="价格（元）", autorange=True),
        height=540,
        margin=dict(l=50, r=20, t=70, b=80),
        template=_TMPL,
        legend=dict(orientation="h", yanchor="top", y=-0.12, x=0.5, xanchor="center"),
        hovermode="x unified",
    )
    return fig


# ── 收益曲线对比 ───────────────────────────────────────────────────────────────

def plot_equity_curve(
    strategy: pd.Series,
    buyhold:   Optional[pd.Series] = None,
    benchmark: Optional[pd.Series] = None,
    initial_cash: float = 100_000.0,
    title: str = "收益曲线对比",
    portfolio_held: Optional[pd.Series] = None,
) -> go.Figure:
    """
    所有 Series 以 initial_cash 归一化。

    portfolio_held : 组合持有期买入持有收益（可选，跳转自组合回测时计算）
    """
    def _norm(s):
        if s is None or s.empty:
            return s
        v0 = s.iloc[0]
        return s if v0 == 0 else s / v0 * initial_cash

    fig = go.Figure()

    s_norm = _norm(strategy)
    fig.add_trace(go.Scatter(
        x=s_norm.index, y=s_norm.values,
        mode="lines", name="策略",
        line=dict(color=_BLUE, width=2.5),
    ))

    if buyhold is not None and not buyhold.empty:
        bh_norm = _norm(buyhold)
        fig.add_trace(go.Scatter(
            x=bh_norm.index, y=bh_norm.values,
            mode="lines", name="买入持有（全程）",
            line=dict(color=_AMBER, width=1.8),
            opacity=0.7,
        ))

    if benchmark is not None and not benchmark.empty:
        bm_norm = _norm(benchmark)
        fig.add_trace(go.Scatter(
            x=bm_norm.index, y=bm_norm.values,
            mode="lines", name="沪深300",
            line=dict(color=_PURPLE, width=1.5),
            opacity=0.7,
        ))

    if portfolio_held is not None and not portfolio_held.empty:
        ph_norm = _norm(portfolio_held)
        fig.add_trace(go.Scatter(
            x=ph_norm.index, y=ph_norm.values,
            mode="lines", name="组合实际持仓收益",
            line=dict(color=_GREEN, width=1.8),
            opacity=0.8,
        ))

    fig.update_layout(
        title=dict(text=title, x=0.5, xanchor="center"),
        xaxis=dict(
            title="日期",
            rangeselector=_rangeselector(),
            rangeslider=dict(visible=False),
            type="date",
        ),
        yaxis=dict(
            title="资产（元）",
        ),
        height=420,
        margin=dict(l=60, r=20, t=80, b=50),
        template=_TMPL,
        hovermode="x unified",
        legend=dict(
            orientation="h",
            yanchor="bottom", y=1.08,
            xanchor="left", x=0,
            bgcolor="rgba(0,0,0,0)",
            font=dict(size=12),
        ),
    )
    return fig


# ── 回撤曲线 ──────────────────────────────────────────────────────────────────

def plot_drawdown(
    equity: pd.Series,
    title: str = "回撤曲线",
) -> go.Figure:
    rolling_max = equity.cummax()
    drawdown = (equity - rolling_max) / rolling_max * 100

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=drawdown.index, y=drawdown.values,
        mode="lines", name="回撤",
        line=dict(color=_RED, width=1.5),
        fill="tozeroy",
        fillcolor="rgba(239,83,80,0.18)",
    ))

    fig.update_layout(
        title=dict(text=title, x=0.5, xanchor="center"),
        xaxis=dict(
            title="日期",
            rangeselector=_rangeselector(activecolor="#ef5350"),
            rangeslider=dict(visible=True, thickness=0.04),
            type="date",
        ),
        yaxis_title="回撤（%）",
        height=330,
        margin=dict(l=50, r=20, t=70, b=40),
        template=_TMPL,
        hovermode="x unified",
    )
    return fig


# ── 模拟交易净值走势 ──────────────────────────────────────────────────────────

def plot_nav_history(
    nav_history: List[Dict],
    title: str = "模拟净值走势",
) -> go.Figure:
    fig = go.Figure()

    if not nav_history:
        fig.update_layout(
            title=title, height=280, template=_TMPL,
            annotations=[dict(text="暂无净值数据", xref="paper", yref="paper",
                               x=0.5, y=0.5, showarrow=False, font=dict(size=16))],
        )
        return fig

    df = pd.DataFrame(nav_history)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")

    fig.add_trace(go.Scatter(
        x=df["date"], y=df["nav"],
        mode="lines+markers", name="净值",
        line=dict(color=_GREEN, width=2),
        marker=dict(size=5),
        hovertemplate="%{x|%Y-%m-%d}<br>净值: %{y:.4f}<br>总资产: %{customdata:,.0f}<extra></extra>",
        customdata=df["total"].values,
    ))

    fig.add_hline(y=1.0, line_dash="dash", line_color="gray",
                  annotation_text="初始净值 1.0", annotation_position="right")

    fig.update_layout(
        title=title, xaxis_title="日期", yaxis_title="净值",
        height=280, margin=dict(l=50, r=20, t=50, b=40),
        template=_TMPL, hovermode="x unified",
    )
    return fig
