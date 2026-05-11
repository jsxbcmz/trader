"""风控审核器：对策略信号进行强制校验，不合格直接否决。"""

from __future__ import annotations

from dataclasses import dataclass

from core.strategy.base import StrategyContext
from core.strategy.signal import Signal


@dataclass
class RiskConfig:
    """风控参数配置"""

    max_stop_loss_pct: float = 0.10
    """单笔最大止损幅度（默认10%）"""

    max_single_position_ratio: float = 1.0
    """单票仓位上限（占总资产比例）"""

    max_total_position_ratio: float = 1.0
    """总仓位上限"""

    daily_loss_limit: float = 0.05
    """日亏损熔断阈值（占总资产）"""

    max_consecutive_losses: int = 3
    """连续亏损停仓次数"""


class RiskGuard:
    """风控审核器

    职责：
    - 对每个交易信号做强制校验
    - 不合格的信号直接否决并记录原因
    """

    def __init__(self, config: RiskConfig | None = None):
        self.config = config or RiskConfig()
        self.daily_loss: float = 0.0
        self.consecutive_losses: int = 0
        self.rejected_log: list[tuple[Signal, str]] = []
        self._last_date: str = ""

    def check(self, signal: Signal, context: StrategyContext) -> tuple[bool, str]:
        """审核信号

        Returns:
            (是否通过, 拒绝原因)
        """
        # 日切时重置日亏损
        if context.current_date != self._last_date:
            self.daily_loss = 0.0
            self._last_date = context.current_date

        # 只对买入信号做开仓检查
        if signal.direction == "BUY":
            # R1: 止损幅度检查
            if signal.stop_loss is not None and signal.price > 0:
                loss_pct = abs(signal.price - signal.stop_loss) / signal.price
                if loss_pct > self.config.max_stop_loss_pct:
                    reason = f"止损幅度{loss_pct:.1%}超过上限{self.config.max_stop_loss_pct:.1%}"
                    self._reject(signal, reason)
                    return False, reason

            # R2: 单票仓位上限
            if context.total_assets > 0:
                buy_value = signal.price * signal.quantity
                position_ratio = buy_value / context.total_assets
                if position_ratio > self.config.max_single_position_ratio:
                    reason = f"单票仓位{position_ratio:.1%}超过上限{self.config.max_single_position_ratio:.1%}"
                    self._reject(signal, reason)
                    return False, reason

            # R3: 总仓位上限
            if context.total_assets > 0:
                current_position_value = context.total_assets - context.available_cash
                new_position_value = current_position_value + signal.price * signal.quantity
                total_ratio = new_position_value / context.total_assets
                if total_ratio > self.config.max_total_position_ratio:
                    reason = f"总仓位{total_ratio:.1%}超过上限{self.config.max_total_position_ratio:.1%}"
                    self._reject(signal, reason)
                    return False, reason

            # R4: 日亏损熔断
            if context.total_assets > 0:
                daily_loss_ratio = self.daily_loss / context.total_assets
                if daily_loss_ratio >= self.config.daily_loss_limit:
                    reason = f"日亏损{daily_loss_ratio:.1%}触发熔断"
                    self._reject(signal, reason)
                    return False, reason

            # R5: 连续亏损停仓
            if self.consecutive_losses >= self.config.max_consecutive_losses:
                reason = f"连续亏损{self.consecutive_losses}次，暂停开仓"
                self._reject(signal, reason)
                return False, reason

        return True, ""

    def record_trade_result(self, pnl: float) -> None:
        """记录交易结果（用于更新连续亏损计数和日亏损）"""
        if pnl < 0:
            self.consecutive_losses += 1
            self.daily_loss += abs(pnl)
        else:
            self.consecutive_losses = 0

    def reset(self) -> None:
        """重置风控状态"""
        self.daily_loss = 0.0
        self.consecutive_losses = 0
        self.rejected_log.clear()
        self._last_date = ""

    def _reject(self, signal: Signal, reason: str) -> None:
        self.rejected_log.append((signal, reason))
