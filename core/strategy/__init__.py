"""策略框架模块：统一策略基类、信号模型与风控审核。

便捷 re-export：调用方可用 ``from core.strategy import BaseStrategy``
等短路径，免去三层包深度。仍兼容显式子模块导入
（如 ``from core.strategy.base import BaseStrategy``）。
"""

from core.strategy.base import BaseStrategy, PositionInfo, StrategyContext
from core.strategy.risk_guard import RiskConfig, RiskGuard
from core.strategy.signal import Signal

__all__ = [
    "BaseStrategy",
    "PositionInfo",
    "RiskConfig",
    "RiskGuard",
    "Signal",
    "StrategyContext",
]
