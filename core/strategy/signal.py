"""交易信号模型。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass
class Signal:
    """策略产生的交易信号"""

    strategy_id: str
    """策略标识"""

    direction: Literal["BUY", "SELL"]
    """方向: BUY 买入 / SELL 卖出"""

    price: float
    """信号触发价格"""

    quantity: int
    """数量（股）"""

    symbol: str = ""
    """股票代码（单票回测时可为空，由引擎填充）"""

    stop_loss: float | None = None
    """止损价"""

    take_profit: float | None = None
    """止盈价"""

    reason: str = ""
    """信号触发原因"""

    score: float = 0.0
    """信号评分（与选股评分系统衔接）"""
