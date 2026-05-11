"""策略注册表：管理策略的注册、查询与生命周期。"""

from __future__ import annotations

from core.strategy.base import BaseStrategy


class StrategyRegistry:
    """策略注册表

    支持动态注册/注销策略，为回测引擎和进化系统提供策略管理能力。
    """

    def __init__(self):
        self._strategies: dict[str, BaseStrategy] = {}

    def register(self, strategy: BaseStrategy) -> None:
        """注册策略"""
        if strategy.strategy_id in self._strategies:
            raise ValueError(f"策略ID已存在: {strategy.strategy_id}")
        self._strategies[strategy.strategy_id] = strategy

    def unregister(self, strategy_id: str) -> None:
        """注销策略"""
        self._strategies.pop(strategy_id, None)

    def get(self, strategy_id: str) -> BaseStrategy | None:
        """按ID获取策略"""
        return self._strategies.get(strategy_id)

    def list_all(self) -> list[BaseStrategy]:
        """获取所有已注册策略"""
        return list(self._strategies.values())

    def list_ids(self) -> list[str]:
        """获取所有策略ID"""
        return list(self._strategies.keys())

    def clear(self) -> None:
        """清空注册表"""
        self._strategies.clear()

    def __len__(self) -> int:
        return len(self._strategies)

    def __contains__(self, strategy_id: str) -> bool:
        return strategy_id in self._strategies
