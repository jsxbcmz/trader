"""
Julie 量化 — A股策略工具
启动方式：
    cd 07-A-Stock-High_level-stratage
    streamlit run web/app.py
"""
import sys
from pathlib import Path

# 将项目根目录加入 Python 路径，确保 core / strategies / config 可被导入
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

import streamlit as st

st.set_page_config(
    page_title="Julie 量化",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── 全局样式 ────────────────────────────────────────────────────────────────────
st.markdown(
    """
    <style>
    /* 顶部 logo + nav 行 */
    .nav-logo { font-size: 1.5rem; font-weight: 700; }
    /* 指标卡片 */
    .metric-positive { color: #ef5350 !important; }
    .metric-negative { color: #26a69a !important; }
    /* 去掉导航栏 radio 的上边距（仅限顶部导航列，通过列位置限定） */
    div[data-testid="column"]:nth-child(2) div[data-testid="stRadio"] { margin-top: -1rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ── 顶部导航 ────────────────────────────────────────────────────────────────────
col_logo, col_nav, _ = st.columns([2, 3, 5])
with col_logo:
    st.markdown('<span class="nav-logo">📈 Julie 量化</span>', unsafe_allow_html=True)
with col_nav:
    page = st.radio(
        "nav",
        ["回测", "模拟交易"],
        horizontal=True,
        key="nav_page",
        label_visibility="collapsed",
    )

st.divider()

# ── 页面路由 ────────────────────────────────────────────────────────────────────
if page == "回测":
    from web.pages import backtest
    backtest.render()
else:
    from web.pages import paper_trading
    paper_trading.render()
