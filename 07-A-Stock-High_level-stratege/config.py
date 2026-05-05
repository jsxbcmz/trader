"""全局配置 — 策略参数 + 回测/模拟交易默认值"""

# ── 策略参数 ────────────────────────────────────────────────────────────────────

STRATEGY_15X = {
    "min_roe":    0.15,    # 净资产收益率下限
    "min_roa":    0.10,    # 总资产净利率下限
    "stock_num":  10,      # 持仓数量
    "max_price":  200.0,   # 最高股价过滤（元）
    "limit_days": 30,      # 涨停黑名单天数
}

STRATEGY_ROTATION = {
    "min_roe":    0.10,
    "stock_num":  3,
    "max_price":  300.0,
}

STRATEGY_SUPERTREND = {
    "period":     14,      # ATR 计算周期
    "multiplier": 3.0,     # ATR 乘数
    "commission": 0.0003,  # 单向佣金率
    "stamp_duty": 0.001,   # 印花税（仅卖出）
}

# ── 回测默认值 ──────────────────────────────────────────────────────────────────

DEFAULT_INITIAL_CASH = 100_000.0
DEFAULT_COMMISSION   = 0.0003
DEFAULT_STAMP_DUTY   = 0.001
DEFAULT_SLIPPAGE     = 0.001

# ── 策略注册表（显示名 → 内部 ID）────────────────────────────────────────────────

STRATEGY_REGISTRY = {
    "SuperTrend（趋势跟踪）": "supertrend",
    "小市值ROE（15x策略）":   "15x",
    "大盘轮动":               "rotation",
}

# ── 基准指数 ────────────────────────────────────────────────────────────────────

INDEX_BENCHMARKS = {
    "沪深300": "000300",
    "中证500": "000905",
}
