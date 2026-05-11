"""进化记忆：记录每轮进化的策略和绩效，支持回溯查询。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.evolution.evaluator import EvalResult
from core.evolution.generator import GeneratedStrategy


@dataclass
class EvolutionRecord:
    """单轮进化记录"""

    round_num: int
    strategy: GeneratedStrategy
    eval_result: EvalResult
    timestamp: str = ""


class EvolutionMemory:
    """进化记忆存储

    记录所有轮次的策略和绩效，支持：
    - 查询最佳策略
    - 导出/导入历史
    - 进化趋势分析
    """

    def __init__(self):
        self._records: list[EvolutionRecord] = []

    def record(self, record: EvolutionRecord) -> None:
        """记录一轮进化结果"""
        self._records.append(record)

    def get_all_records(self) -> list[EvolutionRecord]:
        """获取所有记录"""
        return list(self._records)

    def get_best(self) -> EvolutionRecord | None:
        """获取绩效最优的记录"""
        if not self._records:
            return None
        return max(self._records, key=lambda r: self._fitness(r.eval_result))

    def get_latest(self) -> EvolutionRecord | None:
        """获取最新记录"""
        return self._records[-1] if self._records else None

    def get_round(self, round_num: int) -> EvolutionRecord | None:
        """按轮次获取记录"""
        for record in self._records:
            if record.round_num == round_num:
                return record
        return None

    def clear(self) -> None:
        """清空记忆"""
        self._records.clear()

    def export_json(self, filepath: Path) -> None:
        """导出记忆到JSON文件"""
        data = []
        for record in self._records:
            data.append({
                "round": record.round_num,
                "timestamp": record.timestamp,
                "strategy": {
                    "id": record.strategy.strategy_id,
                    "name": record.strategy.name,
                    "buy_expr": record.strategy.buy_expr,
                    "sell_expr": record.strategy.sell_expr,
                    "description": record.strategy.description,
                },
                "eval": record.eval_result.to_dict(),
            })
        filepath.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    @property
    def total_rounds(self) -> int:
        return len(self._records)

    def _fitness(self, eval_result: EvalResult) -> float:
        """计算适应度得分"""
        score = eval_result.win_rate * 30
        score += min(eval_result.profit_loss_ratio, 3.0) / 3.0 * 30
        score += max(0, 1.0 - eval_result.max_drawdown / 0.3) * 20
        score += eval_result.sharpe_ratio * 10
        if eval_result.total_trades >= 5:
            score += 10
        return score
