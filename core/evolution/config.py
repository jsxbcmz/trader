"""策略进化配置。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EvolutionConfig:
    """进化系统配置"""

    max_rounds: int = 5
    """最大进化轮次"""

    min_win_rate: float = 0.5
    """最低胜率门槛"""

    min_profit_loss_ratio: float = 1.5
    """最低盈亏比门槛"""

    max_drawdown_limit: float = 0.2
    """最大回撤限制"""

    eval_stock_pool_size: int = 50
    """评估股票池大小"""

    eval_period_days: int = 180
    """评估时间窗口（天）"""

    llm_model: str = "qwen3-30b"
    """LLM模型名称"""

    llm_base_url: str = ""
    """LLM API地址"""

    llm_api_key: str = ""
    """LLM API密钥"""

    llm_temperature: float = 0.7
    """生成温度"""
