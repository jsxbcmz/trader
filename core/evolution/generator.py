"""策略生成器：基于LLM自动生成通达信公式策略。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.evolution.config import EvolutionConfig


@dataclass
class GeneratedStrategy:
    """LLM生成的策略"""

    strategy_id: str
    name: str
    buy_expr: str
    sell_expr: str
    description: str = ""
    generation_round: int = 0
    raw_llm_response: str = ""
    strategy_type: str = "expression"  # "expression" 或 "brick_pattern"
    params: dict[str, Any] = field(default_factory=dict)  # 砖形图参数等


GENERATE_PROMPT_TEMPLATE = """你是一个专业的A股量化策略工程师。请根据用户的策略意图，生成通达信风格的买入和卖出条件表达式。

## 可用函数
MA(序列, 周期), EMA(序列, 周期), SMA(序列, N, M)
HHV(序列, 周期), LLV(序列, 周期)
REF(序列, 周期), CROSS(序列A, 序列B)
COUNT(条件, 周期), SUM(序列, 周期)
IF(条件, 值A, 值B), MAX(A, B), MIN(A, B), ABS(序列)
STD(序列, 周期), EVERY(条件, 周期), BETWEEN(值, 下限, 上限)

## 可用字段
CLOSE, OPEN, HIGH, LOW, VOLUME

## 输出格式（严格JSON）
{{
    "name": "策略名称",
    "buy_expr": "买入条件表达式",
    "sell_expr": "卖出条件表达式",
    "description": "策略逻辑说明"
}}

## 用户意图
{intent}

## 约束条件
{constraints}

请直接输出JSON，不要有其他文字。"""

OPTIMIZE_PROMPT_TEMPLATE = """你是一个专业的A股量化策略工程师。当前策略的回测效果不理想，请根据绩效反馈优化策略。

## 当前策略
- 名称: {name}
- 买入条件: {buy_expr}
- 卖出条件: {sell_expr}

## 回测绩效
- 总收益率: {total_return:.2%}
- 胜率: {win_rate:.2%}
- 盈亏比: {profit_loss_ratio:.2f}
- 最大回撤: {max_drawdown:.2%}
- 交易次数: {total_trades}

## 问题分析
{analysis}

## 可用函数
MA(序列, 周期), EMA(序列, 周期), SMA(序列, N, M)
HHV(序列, 周期), LLV(序列, 周期)
REF(序列, 周期), CROSS(序列A, 序列B)
COUNT(条件, 周期), SUM(序列, 周期)
IF(条件, 值A, 值B), MAX(A, B), MIN(A, B), ABS(序列)
STD(序列, 周期), EVERY(条件, 周期), BETWEEN(值, 下限, 上限)

## 可用字段
CLOSE, OPEN, HIGH, LOW, VOLUME

## 输出格式（严格JSON）
{{
    "name": "优化后策略名称",
    "buy_expr": "优化后买入条件",
    "sell_expr": "优化后卖出条件",
    "description": "优化说明"
}}

请直接输出JSON，不要有其他文字。"""


BRICK_GENERATE_PROMPT_TEMPLATE = """你是一个专业的A股量化策略工程师，精通砖形图交易定式。
请根据用户的策略意图，生成砖形图策略的参数配置。

## 砖形图策略说明
砖形图策略有以下可配置参数：
1. patterns: 启用的定式类型列表，可选值为 "N_SHAPE_JUMP"(N型起跳)、"SIDEWAYS_JUMP"(横盘起跳)、"UPTREND_CONTINUE"(上升波段延续)
2. min_grade: 最低评分等级，可选值为 "S"(≥90分)、"A"(≥75分)、"B"(≥60分)、"C"(≥45分)、"D"(≥0分)
3. buy_ratio: 买入资金比例，0.0~1.0之间

## 三种定式特点
- N型起跳：适合超跌反弹，前段上涨后回调到KDJ超卖区再翻红，成功率较高但频率低
- 横盘起跳：适合蓄势突破，红绿频繁交替后突然放量翻红，爆发力强但假突破风险大
- 上升波段延续：适合追强势趋势，连续红砖后极短回调再翻红，胜率高但买点偏高

## 输出格式（严格JSON）
{{
    "name": "策略名称",
    "patterns": ["N_SHAPE_JUMP", "SIDEWAYS_JUMP", "UPTREND_CONTINUE"],
    "min_grade": "B",
    "buy_ratio": 1.0,
    "description": "策略逻辑说明"
}}

## 用户意图
{intent}

## 约束条件
{constraints}

请直接输出JSON，不要有其他文字。"""

BRICK_OPTIMIZE_PROMPT_TEMPLATE = """你是一个专业的A股量化策略工程师，精通砖形图交易定式。
当前砖形图策略的回测效果不理想，请根据绩效反馈优化参数配置。

## 当前策略配置
- 名称: {name}
- 启用定式: {patterns}
- 最低评分等级: {min_grade}
- 买入资金比例: {buy_ratio}

## 回测绩效
- 总收益率: {total_return:.2%}
- 胜率: {win_rate:.2%}
- 盈亏比: {profit_loss_ratio:.2f}
- 最大回撤: {max_drawdown:.2%}
- 交易次数: {total_trades}

## 问题分析
{analysis}

## 优化建议方向
- 如果交易次数太少：降低min_grade(如B→C)，或增加更多定式类型
- 如果胜率太低：提高min_grade(如B→A)，或只保留成功率高的定式(如N型起跳)
- 如果回撤太大：提高min_grade，减少buy_ratio，或去掉风险大的定式(如横盘起跳)
- 如果盈亏比低：保留爆发力强的定式(横盘起跳、波段延续)

## 输出格式（严格JSON）
{{
    "name": "优化后策略名称",
    "patterns": ["N_SHAPE_JUMP", "SIDEWAYS_JUMP", "UPTREND_CONTINUE"],
    "min_grade": "B",
    "buy_ratio": 1.0,
    "description": "优化说明"
}}

请直接输出JSON，不要有其他文字。"""


def _is_brick_intent(intent: str) -> bool:
    """判断用户意图是否与砖形图策略相关"""
    keywords = ["砖形图", "砖形", "N型起跳", "横盘起跳", "波段延续", "绿转红", "翻红力度"]
    return any(kw in intent for kw in keywords)


class StrategyGenerator:
    """基于LLM的策略代码生成器"""

    def __init__(self, config: EvolutionConfig):
        self.config = config
        self._generation_counter = 0

    def generate(self, intent: str, constraints: dict[str, Any] | None = None) -> GeneratedStrategy:
        """根据用户意图生成策略

        Args:
            intent: 用户描述的策略意图
            constraints: 约束条件（如最大持仓天数等）

        Returns:
            生成的策略
        """
        is_brick = _is_brick_intent(intent)
        constraints_text = json.dumps(constraints or {}, ensure_ascii=False)

        if is_brick:
            prompt = BRICK_GENERATE_PROMPT_TEMPLATE.format(intent=intent, constraints=constraints_text)
            response = self._call_llm(prompt)
            strategy = self._parse_brick_response(response)
        else:
            prompt = GENERATE_PROMPT_TEMPLATE.format(intent=intent, constraints=constraints_text)
            response = self._call_llm(prompt)
            strategy = self._parse_response(response)

        self._generation_counter += 1
        strategy.generation_round = self._generation_counter
        return strategy

    def optimize(self, strategy: GeneratedStrategy, eval_result: dict) -> GeneratedStrategy:
        """根据回测反馈优化策略

        Args:
            strategy: 当前策略
            eval_result: 回测绩效指标字典

        Returns:
            优化后的策略
        """
        analysis = self._analyze_performance(eval_result)

        if strategy.strategy_type == "brick_pattern":
            params = strategy.params
            prompt = BRICK_OPTIMIZE_PROMPT_TEMPLATE.format(
                name=strategy.name,
                patterns=", ".join(params.get("patterns", [])),
                min_grade=params.get("min_grade", "B"),
                buy_ratio=params.get("buy_ratio", 1.0),
                total_return=eval_result.get("total_return", 0),
                win_rate=eval_result.get("win_rate", 0),
                profit_loss_ratio=eval_result.get("profit_loss_ratio", 0),
                max_drawdown=eval_result.get("max_drawdown", 0),
                total_trades=eval_result.get("total_trades", 0),
                analysis=analysis,
            )
            response = self._call_llm(prompt)
            new_strategy = self._parse_brick_response(response)
        else:
            prompt = OPTIMIZE_PROMPT_TEMPLATE.format(
                name=strategy.name,
                buy_expr=strategy.buy_expr,
                sell_expr=strategy.sell_expr,
                total_return=eval_result.get("total_return", 0),
                win_rate=eval_result.get("win_rate", 0),
                profit_loss_ratio=eval_result.get("profit_loss_ratio", 0),
                max_drawdown=eval_result.get("max_drawdown", 0),
                total_trades=eval_result.get("total_trades", 0),
                analysis=analysis,
            )
            response = self._call_llm(prompt)
            new_strategy = self._parse_response(response)

        self._generation_counter += 1
        new_strategy.generation_round = self._generation_counter
        return new_strategy

    def _call_llm(self, prompt: str) -> str:
        """调用LLM获取响应，支持 OpenAI 兼容接口和 Anthropic Messages API。"""
        try:
            import requests

            base_url = self.config.llm_base_url.rstrip("/")
            is_anthropic = "anthropic" in base_url.lower()

            if is_anthropic:
                headers = {
                    "Content-Type": "application/json",
                    "x-api-key": self.config.llm_api_key,
                    "anthropic-version": "2023-06-01",
                    "x-idealab-session-id": "strategy-evolution",
                }
                payload = {
                    "model": self.config.llm_model,
                    "max_tokens": 4096,
                    "messages": [{"role": "user", "content": prompt}],
                }
                url = f"{base_url}/v1/messages"
                response = requests.post(url, headers=headers, json=payload, timeout=120)
                response.raise_for_status()
                data = response.json()
                return data["content"][0]["text"]
            else:
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.config.llm_api_key}",
                }
                payload = {
                    "model": self.config.llm_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": self.config.llm_temperature,
                }
                url = f"{base_url}/chat/completions"
                response = requests.post(url, headers=headers, json=payload, timeout=120)
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"]
        except Exception as exc:
            raise RuntimeError(f"LLM调用失败: {exc}") from exc

    def _parse_response(self, response: str) -> GeneratedStrategy:
        """解析LLM响应为表达式策略对象"""
        data = self._extract_json(response)
        strategy_id = f"EVO_{self._generation_counter:03d}"
        return GeneratedStrategy(
            strategy_id=strategy_id,
            name=data.get("name", "未命名策略"),
            buy_expr=data.get("buy_expr", ""),
            sell_expr=data.get("sell_expr", ""),
            description=data.get("description", ""),
            raw_llm_response=response,
            strategy_type="expression",
        )

    def _parse_brick_response(self, response: str) -> GeneratedStrategy:
        """解析LLM响应为砖形图策略参数配置"""
        data = self._extract_json(response)
        strategy_id = f"EVO_{self._generation_counter:03d}"
        patterns = data.get("patterns", ["N_SHAPE_JUMP", "SIDEWAYS_JUMP", "UPTREND_CONTINUE"])
        min_grade = data.get("min_grade", "B")
        buy_ratio = data.get("buy_ratio", 1.0)

        params = {
            "patterns": patterns,
            "min_grade": min_grade,
            "buy_ratio": buy_ratio,
        }

        return GeneratedStrategy(
            strategy_id=strategy_id,
            name=data.get("name", "砖形图策略"),
            buy_expr=f"砖形图定式: {', '.join(patterns)} | 最低评级: {min_grade}",
            sell_expr="砖形图转绿且力度达标 / 跌破多空线",
            description=data.get("description", ""),
            raw_llm_response=response,
            strategy_type="brick_pattern",
            params=params,
        )

    def _extract_json(self, response: str) -> dict:
        """从LLM响应中提取JSON"""
        text = response.strip()
        if "```" in text:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                text = text[start:end]

        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"LLM响应解析失败: {exc}\n原始响应: {response}") from exc

    def _analyze_performance(self, eval_result: dict) -> str:
        """分析绩效问题"""
        issues = []
        win_rate = eval_result.get("win_rate", 0)
        plr = eval_result.get("profit_loss_ratio", 0)
        max_dd = eval_result.get("max_drawdown", 0)
        total_trades = eval_result.get("total_trades", 0)

        if win_rate < 0.4:
            issues.append("胜率过低，买入条件不够精确，需要增加过滤条件")
        if plr < 1.0:
            issues.append("盈亏比不佳，止盈条件可能过早或止损过晚")
        if max_dd > 0.15:
            issues.append("回撤过大，需要加强风控或减少追高")
        if total_trades < 5:
            issues.append("交易次数过少，条件过于严格，适当放宽")
        if total_trades > 100:
            issues.append("交易过于频繁，条件过于宽松，需要收紧")

        return "\n".join(issues) if issues else "各项指标基本合格，可微调优化"
