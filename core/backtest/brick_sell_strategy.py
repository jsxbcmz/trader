"""砖形图专属卖出策略。

修改止盈/止损规则只需编辑本文件。

核心交易纪律（按优先级排列）：
1. 止损位：买入当天K线最低价，跌破无条件离场
2. 绿砖止损：砖型图值下降，清仓
3. 时间止损：2个交易日内不拉起来就走，防范资金效率降低
4. 持仓限制：超短线策略，最多持仓5天，不做波段
5. 分批止盈：涨幅达到阈值时分批卖出
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from core.backtest.models import BacktestHolding, SellAction, SellSignal
from core.backtest.sell_strategy import SellStrategy


@dataclass
class BrickChartSellStrategy(SellStrategy):
    """砖形图超短线卖出策略

    止损规则（满足任一即清仓，按优先级排列）：
    1. 破买入日最低价：收盘价 < 买入当天K线最低价 → 无条件离场
    2. 绿砖止损：砖型图值下降 → 清仓
    3. 时间止损：持仓 ≥ 2个交易日且未盈利（收盘价 ≤ 成本价）→ 清仓
    4. 持仓超限：持仓 ≥ max_hold_days 天 → 强制清仓（超短线不做波段）

    止盈规则：
    - 分批止盈：涨幅 ≥ 阈值时卖出 1/4，阈值逐级递增
    """

    # ── 可调参数 ──
    partial_profit_threshold: float = 0.03   # 分批止盈阈值（3%）
    partial_sell_ratio: float = 0.25         # 分批卖出比例（1/4）
    time_stop_days: int = 2                  # 时间止损天数（2个交易日不拉起来就走）
    max_hold_days: int = 5                   # 最大持仓天数（超短线，4-6天取中值5）

    def __post_init__(self):
        self._brick_cache: dict[str, np.ndarray | None] = {}

    def _calc_hold_days(self, holding: BacktestHolding, current_index: int) -> int:
        """计算持有交易日数（买入当天 = 0，次日 = 1，...）"""
        if holding.buy_data_index < 0:
            return 0
        return current_index - holding.buy_data_index

    def should_sell(
        self,
        holding: BacktestHolding,
        daily_data: pd.DataFrame,
        current_index: int,
    ) -> SellSignal:
        if current_index < 1:
            return SellSignal(action=SellAction.HOLD)

        current_close = daily_data.iloc[current_index]["close"]
        hold_days = self._calc_hold_days(holding, current_index)

        # ━━ 纪律1：止损位 = 买入当天K线最低价，跌破无条件离场 ━━
        if holding.buy_day_low > 0 and current_close < holding.buy_day_low:
            return SellSignal(
                action=SellAction.CLEAR,
                reason=f"破买入日低点止损({holding.buy_day_low:.2f})",
            )

        # ━━ 绿砖止损：砖型图值下降 ━━
        cache_key = holding.symbol
        if cache_key not in self._brick_cache:
            self._brick_cache[cache_key] = self._calc_brick_series(daily_data)
        brick_values = self._brick_cache[cache_key]

        if brick_values is not None and current_index < len(brick_values):
            current_brick = brick_values[current_index]
            prev_brick = brick_values[current_index - 1]
            if current_brick < prev_brick:
                # 计算刚好变绿砖的临界价格
                threshold = self._calc_green_brick_price(
                    daily_data, current_index, prev_brick,
                )
                return SellSignal(
                    action=SellAction.CLEAR,
                    reason=f"绿砖止损(临界{threshold:.2f})" if threshold else "绿砖止损",
                    price=threshold,
                )

        # ━━ 纪律2：时间止损，N个交易日内不拉起来就走 ━━
        if hold_days >= self.time_stop_days and current_close <= holding.cost_price:
            return SellSignal(
                action=SellAction.CLEAR,
                reason=f"时间止损({hold_days}天未盈利)",
            )

        # ━━ 纪律3：持仓超限，超短线不做波段 ━━
        if hold_days >= self.max_hold_days:
            return SellSignal(
                action=SellAction.CLEAR,
                reason=f"持仓{hold_days}天超限清仓",
            )

        # ━━ 分批止盈：涨幅达到阈值时分批卖出 ━━
        current_high = float(daily_data.iloc[current_index]["high"])
        profit_rate = (current_high - holding.cost_price) / holding.cost_price
        next_threshold = self.partial_profit_threshold * (holding.partial_sell_count + 1)
        if profit_rate >= next_threshold:
            return SellSignal(
                action=SellAction.PARTIAL,
                ratio=self.partial_sell_ratio,
                reason=f"分批止盈(第{holding.partial_sell_count + 1}次,阈值{next_threshold * 100:.1f}%)",
            )

        return SellSignal(action=SellAction.HOLD)

    def get_display_params(self) -> dict[str, str]:
        return {
            "止损位": "买入日K线最低价",
            "时间止损": f"{self.time_stop_days}天未盈利离场",
            "最大持仓": f"{self.max_hold_days}天",
            "分批止盈阈值": f"{self.partial_profit_threshold * 100:.1f}%",
            "分批卖出比例": f"{self.partial_sell_ratio * 100:.0f}%",
            "止损条件": "破低点 / 绿砖 / 时间止损",
        }

    def _calc_green_brick_price(
        self,
        daily_data: pd.DataFrame,
        current_index: int,
        prev_brick: float,
    ) -> float | None:
        """计算刚好变绿砖的临界收盘价。

        返回使 brick[current_index] == prev_brick 的收盘价，
        若无法计算或超出当天价格范围则返回 None（回退到收盘价）。
        """
        from app.chart_indicators import calc_brick_threshold_price

        if not all(col in daily_data.columns for col in ("high", "low", "close")):
            return None

        high = daily_data["high"].values.astype(float)
        low = daily_data["low"].values.astype(float)
        close = daily_data["close"].values.astype(float)

        threshold = calc_brick_threshold_price(
            high, low, close, current_index, prev_brick,
        )
        if threshold is None:
            # 临界价超出当天范围，使用开盘价（开盘即已绿砖）
            return float(daily_data.iloc[current_index]["open"])
        return threshold

    def _calc_brick_series(self, daily_data: pd.DataFrame) -> np.ndarray | None:
        """计算砖型图指标序列

        严格复用通达信砖型图公式（VAR1A-VAR6A）：
        VAR1A := (HHV(HIGH,4) - CLOSE) / (HHV(HIGH,4) - LLV(LOW,4)) * 100 - 90
        VAR2A := SMA(VAR1A, 4, 1) + 100
        VAR3A := (CLOSE - LLV(LOW,4)) / (HHV(HIGH,4) - LLV(LOW,4)) * 100
        VAR4A := SMA(VAR3A, 6, 1)
        VAR5A := SMA(VAR4A, 6, 1) + 100
        VAR6A := VAR5A - VAR2A
        砖型图 := IF(VAR6A > 4, VAR6A - 4, 0)
        """
        from app.chart_indicators import compute_brick_indicator

        if not all(col in daily_data.columns for col in ("high", "low", "close")):
            return None

        high = daily_data["high"].values.astype(float)
        low = daily_data["low"].values.astype(float)
        close = daily_data["close"].values.astype(float)

        if len(close) < 4:
            return None

        result = compute_brick_indicator(high, low, close)
        return result["brick"]
