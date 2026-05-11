"""策略进化主控制器：协调生成、评估、反馈迭代循环。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from core.evolution.config import EvolutionConfig
from core.evolution.evaluator import EvalResult, StrategyEvaluator
from core.evolution.generator import GeneratedStrategy, StrategyGenerator
from core.evolution.memory import EvolutionMemory, EvolutionRecord


@dataclass
class EvolutionResult:
    """进化最终结果"""

    success: bool
    best_strategy: GeneratedStrategy | None = None
    best_eval: EvalResult | None = None
    total_rounds: int = 0
    history: list[EvolutionRecord] = field(default_factory=list)


class StrategyEvolver:
    """策略进化主控制器

    工作流：
    1. LLM 根据用户意图生成初始策略
    2. 自动回测评估绩效
    3. 若不达标，将绩效反馈给 LLM 迭代优化
    4. 重复直到达标或达到最大轮次
    """

    def __init__(
        self,
        generator: StrategyGenerator,
        evaluator: StrategyEvaluator,
        memory: EvolutionMemory,
        config: EvolutionConfig,
    ):
        self.generator = generator
        self.evaluator = evaluator
        self.memory = memory
        self.config = config

    def evolve(
        self,
        intent: str,
        stock_pool: list[str],
        start_date: str = "",
        end_date: str = "",
        constraints: dict | None = None,
    ) -> EvolutionResult:
        """执行策略进化循环

        Args:
            intent: 用户策略意图描述
            stock_pool: 评估用股票池
            start_date: 评估起始日期
            end_date: 评估结束日期
            constraints: 约束条件

        Returns:
            进化结果
        """
        # 生成初始策略
        strategy = self.generator.generate(intent, constraints)

        best_strategy = strategy
        best_eval: EvalResult | None = None
        best_score = -float("inf")

        for round_num in range(1, self.config.max_rounds + 1):
            # 评估
            eval_result = self.evaluator.evaluate(
                strategy, stock_pool, start_date, end_date
            )

            # 记录到进化记忆
            record = EvolutionRecord(
                round_num=round_num,
                strategy=strategy,
                eval_result=eval_result,
                timestamp=datetime.now().isoformat(),
            )
            self.memory.record(record)

            # 检查是否达标
            score = self._calc_fitness_score(eval_result)
            if score > best_score:
                best_score = score
                best_strategy = strategy
                best_eval = eval_result

            if self._meets_criteria(eval_result):
                return EvolutionResult(
                    success=True,
                    best_strategy=best_strategy,
                    best_eval=best_eval,
                    total_rounds=round_num,
                    history=self.memory.get_all_records(),
                )

            # 未达标则优化
            if round_num < self.config.max_rounds:
                strategy = self.generator.optimize(strategy, eval_result.to_dict())

        return EvolutionResult(
            success=False,
            best_strategy=best_strategy,
            best_eval=best_eval,
            total_rounds=self.config.max_rounds,
            history=self.memory.get_all_records(),
        )

    def _meets_criteria(self, eval_result: EvalResult) -> bool:
        """检查是否满足进化成功标准"""
        if eval_result.total_trades < 3:
            return False
        if eval_result.win_rate < self.config.min_win_rate:
            return False
        if eval_result.profit_loss_ratio < self.config.min_profit_loss_ratio:
            return False
        if eval_result.max_drawdown > self.config.max_drawdown_limit:
            return False
        return True

    def _calc_fitness_score(self, eval_result: EvalResult) -> float:
        """计算适应度得分（用于比较不同轮次的策略优劣）"""
        score = 0.0
        score += eval_result.win_rate * 30
        score += min(eval_result.profit_loss_ratio, 3.0) / 3.0 * 30
        score += max(0, 1.0 - eval_result.max_drawdown / 0.3) * 20
        score += eval_result.sharpe_ratio * 10
        if eval_result.total_trades >= 5:
            score += 10
        return score
