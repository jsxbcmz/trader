"""策略自动评估器：对生成的策略进行回测评估。"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from core.backtest.config import BacktestConfig
from core.backtest.engine import BacktestEngine, BacktestResult
from core.evolution.generator import GeneratedStrategy
from core.models.brick_pattern import PatternType
from core.strategy.builtin.brick_pattern_strategy import BrickPatternStrategy
from core.strategy.builtin.expression_strategy import ExpressionStrategy


@dataclass
class EvalResult:
    """评估结果"""

    total_return: float = 0.0
    win_rate: float = 0.0
    profit_loss_ratio: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    total_trades: int = 0
    annualized_return: float = 0.0
    avg_holding_days: float = 0.0
    individual_results: list[BacktestResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total_return": self.total_return,
            "win_rate": self.win_rate,
            "profit_loss_ratio": self.profit_loss_ratio,
            "max_drawdown": self.max_drawdown,
            "sharpe_ratio": self.sharpe_ratio,
            "total_trades": self.total_trades,
            "annualized_return": self.annualized_return,
            "avg_holding_days": self.avg_holding_days,
        }


class StrategyEvaluator:
    """策略自动评估器

    将生成的策略在指定股票池上进行回测，汇总绩效指标。
    """

    def __init__(self, data_loader, backtest_config: BacktestConfig | None = None):
        """
        Args:
            data_loader: 可调用对象，接收 symbol 返回 pd.DataFrame（日线数据）
            backtest_config: 回测配置
        """
        self.data_loader = data_loader
        self.config = backtest_config or BacktestConfig()

    def evaluate(
        self,
        generated: GeneratedStrategy,
        stock_pool: list[str],
        start_date: str = "",
        end_date: str = "",
    ) -> EvalResult:
        """评估策略

        Args:
            generated: LLM生成的策略
            stock_pool: 评估用股票池
            start_date: 起始日期（为空则不过滤）
            end_date: 结束日期

        Returns:
            汇总评估结果
        """
        if generated.strategy_type == "brick_pattern":
            strategy = self._build_brick_strategy(generated)
        else:
            strategy = ExpressionStrategy(
                strategy_id=generated.strategy_id,
                name=generated.name,
                buy_expr=generated.buy_expr,
                sell_expr=generated.sell_expr,
            )

        results: list[BacktestResult] = []
        for symbol in stock_pool:
            try:
                data = self.data_loader(symbol)
            except Exception:
                continue

            if data is None or len(data) < 60:
                continue

            # 按日期过滤
            if start_date or end_date:
                data = self._filter_date_range(data, start_date, end_date)
                if data is None or len(data) < 30:
                    continue

            engine = BacktestEngine(self.config)
            engine.add_strategy(strategy)
            result = engine.run(symbol, data)
            results.append(result)

        return self._aggregate_results(results)

    def _filter_date_range(
        self, data: pd.DataFrame, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """过滤日期范围"""
        dates = pd.to_datetime(data["date"])
        mask = pd.Series(True, index=data.index)
        if start_date:
            mask &= dates >= pd.Timestamp(start_date)
        if end_date:
            mask &= dates <= pd.Timestamp(end_date)
        filtered = data[mask].reset_index(drop=True)
        return filtered if len(filtered) > 0 else None

    def _aggregate_results(self, results: list[BacktestResult]) -> EvalResult:
        """汇总多只股票的回测结果"""
        if not results:
            return EvalResult()

        valid_results = [r for r in results if r.total_trades > 0]
        if not valid_results:
            return EvalResult(individual_results=results)

        total_return = sum(r.total_return for r in valid_results) / len(valid_results)
        win_rate = sum(r.win_rate for r in valid_results) / len(valid_results)
        plr = sum(r.profit_loss_ratio for r in valid_results) / len(valid_results)
        max_dd = max(r.max_drawdown for r in valid_results)
        sharpe = sum(r.sharpe_ratio for r in valid_results) / len(valid_results)
        total_trades = sum(r.total_trades for r in valid_results)
        ann_return = sum(r.annualized_return for r in valid_results) / len(valid_results)
        avg_hold = sum(r.avg_holding_days for r in valid_results) / len(valid_results)

        return EvalResult(
            total_return=total_return,
            win_rate=win_rate,
            profit_loss_ratio=plr,
            max_drawdown=max_dd,
            sharpe_ratio=sharpe,
            total_trades=total_trades,
            annualized_return=ann_return,
            avg_holding_days=avg_hold,
            individual_results=results,
        )

    def _build_brick_strategy(self, generated: GeneratedStrategy) -> BrickPatternStrategy:
        """根据 LLM 生成的参数构建砖形图策略实例"""
        params = generated.params

        min_grade = params.get("min_grade", "B")
        pattern_names = params.get("patterns", ["N_SHAPE_JUMP", "SIDEWAYS_JUMP", "UPTREND_CONTINUE"])

        pattern_map = {
            "N_SHAPE_JUMP": PatternType.N_SHAPE_JUMP,
            "SIDEWAYS_JUMP": PatternType.SIDEWAYS_JUMP,
            "UPTREND_CONTINUE": PatternType.UPTREND_CONTINUE,
        }
        patterns = tuple(pattern_map[p] for p in pattern_names if p in pattern_map)
        if not patterns:
            patterns = (PatternType.N_SHAPE_JUMP, PatternType.SIDEWAYS_JUMP, PatternType.UPTREND_CONTINUE)

        return BrickPatternStrategy(
            patterns=patterns,
            min_grade=min_grade,
            buy_ratio=params.get("buy_ratio", 1.0),
        )
