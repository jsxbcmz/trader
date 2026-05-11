"""回测引擎配置模型。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BacktestConfig:
    """回测运行配置"""

    initial_capital: float = 100_000.0
    """初始资金（元）"""

    commission_rate: float = 0.00025
    """佣金费率（双向收取），默认万2.5"""

    min_commission: float = 5.0
    """单笔最低佣金（元）"""

    stamp_tax_rate: float = 0.001
    """印花税费率（仅卖出），默认千1"""

    slippage_rate: float = 0.001
    """滑点费率，默认千1"""

    settlement: str = "T+1"
    """结算模式: T+1 或 T+0"""

    max_single_position_ratio: float = 1.0
    """单票最大仓位比例（占总资产），默认100%不限"""

    max_total_position_ratio: float = 1.0
    """总仓位上限比例，默认100%"""

    lot_size: int = 100
    """最小交易单位（手 = 100股）"""

    @property
    def is_t_plus_1(self) -> bool:
        return self.settlement == "T+1"

    def calc_buy_cost(self, price: float, quantity: int) -> float:
        """计算买入总成本（含佣金+滑点）"""
        amount = price * quantity
        slippage = amount * self.slippage_rate
        commission = max(amount * self.commission_rate, self.min_commission)
        return amount + slippage + commission

    def calc_sell_revenue(self, price: float, quantity: int) -> float:
        """计算卖出净收入（扣佣金+印花税+滑点）"""
        amount = price * quantity
        slippage = amount * self.slippage_rate
        commission = max(amount * self.commission_rate, self.min_commission)
        stamp_tax = amount * self.stamp_tax_rate
        return amount - slippage - commission - stamp_tax

    def calc_actual_buy_price(self, price: float) -> float:
        """计算含滑点的实际买入价"""
        return price * (1 + self.slippage_rate)

    def calc_actual_sell_price(self, price: float) -> float:
        """计算含滑点的实际卖出价"""
        return price * (1 - self.slippage_rate)
