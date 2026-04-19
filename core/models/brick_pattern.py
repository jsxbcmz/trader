"""砖形图交易定式选股模型。

定义三种交易定式类型、匹配结果、风险过滤结果等数据结构。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PatternType(Enum):
    """交易定式类型"""

    N_SHAPE_JUMP = "N型起跳"
    SIDEWAYS_JUMP = "横盘起跳"
    UPTREND_CONTINUE = "上升波段延续"


class RiskFilterType(Enum):
    """风险过滤规则类型"""

    LIMIT_DOWN = "一字板跌停"
    LONG_SIDEWAYS = "横盘时间过长"
    HEAVY_VOLUME_DROP = "放量大阴线"


@dataclass(frozen=True)
class PatternMatchDetail:
    """单个定式的匹配详情"""

    pattern_type: PatternType
    matched: bool = False
    description: str = ""
    score: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RiskFilterDetail:
    """单条风险过滤的检测结果"""

    filter_type: RiskFilterType
    triggered: bool = False
    description: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BrickPatternMatch:
    """单只股票的砖形图定式匹配结果"""

    symbol: str
    name: str = ""
    target_date: str = ""
    actual_date: str = ""

    prerequisite_passed: bool = False
    prerequisite_detail: str = ""

    pattern_matches: tuple[PatternMatchDetail, ...] = field(default_factory=tuple)
    risk_filters: tuple[RiskFilterDetail, ...] = field(default_factory=tuple)

    final_matched: bool = False
    matched_pattern: str = ""
    risk_rejected: bool = False
    risk_reason: str = ""

    error: str = ""

    @property
    def matched_pattern_types(self) -> list[PatternType]:
        return [pm.pattern_type for pm in self.pattern_matches if pm.matched]

    @property
    def triggered_risks(self) -> list[RiskFilterType]:
        return [rf.filter_type for rf in self.risk_filters if rf.triggered]

    def format_summary(self) -> str:
        if self.error:
            return f"错误: {self.error}"
        if not self.prerequisite_passed:
            return f"前提不满足: {self.prerequisite_detail}"
        if self.risk_rejected:
            return f"风险过滤: {self.risk_reason}"
        if self.final_matched:
            return f"命中: {self.matched_pattern}"
        return "未命中任何定式"


@dataclass(frozen=True)
class BrickPatternRequest:
    """砖形图定式选股请求"""

    target_date: str
    stock_pool_name: str = "default"
    symbols: tuple[str, ...] = field(default_factory=tuple)
    enabled_patterns: tuple[PatternType, ...] = (
        PatternType.N_SHAPE_JUMP,
        PatternType.SIDEWAYS_JUMP,
        PatternType.UPTREND_CONTINUE,
    )
    price_limit: float = 0.0


@dataclass(frozen=True)
class BrickPatternResult:
    """砖形图定式选股结果"""

    request: BrickPatternRequest
    matches: tuple[BrickPatternMatch, ...] = field(default_factory=tuple)
    total: int = 0
    matched_count: int = 0
    risk_filtered_count: int = 0
    error_count: int = 0

    @property
    def hit_matches(self) -> list[BrickPatternMatch]:
        return [m for m in self.matches if m.final_matched]

    @property
    def filtered_matches(self) -> list[BrickPatternMatch]:
        return [m for m in self.matches if m.risk_rejected]
