"""砖形图交易定式选股模型。

定义三种交易定式类型、匹配结果、风险过滤结果等数据结构。
V3：定式专属(30分) + 通用质量(30分) + MACD环境(25分) + 信号强度(15分) + 风险扣分。
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
    HEAVY_VOLUME_DROP = "高位放量大阴线"
    TREND_BROKEN = "短趋势跌破多空线"
    CHASE_HIGH = "追高离心"
    GREEN_VOLUME_UP = "绿砖期放量"
    PEAK_VOLUME = "天量见顶"
    TREND_EXHAUST = "趋势衰竭"
    FAKE_SIDEWAYS = "假横盘"
    HAMMER = "锤子线"
    LARGE_UPPER_SHADOW = "大上影线"
    THIRD_WAVE = "三波追高"
    HIGH_BRICK_SIDEWAYS = "高砖值横盘"


@dataclass(frozen=True)
class ScoreBreakdown:
    """评分分解详情"""

    specific_score: float = 0.0
    specific_items: dict[str, float] = field(default_factory=dict)

    common_score: float = 0.0
    common_items: dict[str, float] = field(default_factory=dict)

    macd_score: float = 0.0
    macd_items: dict[str, float] = field(default_factory=dict)

    signal_score: float = 0.0
    signal_items: dict[str, float] = field(default_factory=dict)

    risk_penalty: float = 0.0
    risk_items: dict[str, float] = field(default_factory=dict)

    # P3 战法加分（红柱比、地量、金叉时间细化等）；默认 0 向后兼容
    bonus_score: float = 0.0
    bonus_items: dict[str, float] = field(default_factory=dict)

    @property
    def base_score(self) -> float:
        return self.specific_score + self.common_score + self.macd_score + self.signal_score

    @property
    def final_score(self) -> float:
        return max(0.0, self.base_score + self.bonus_score + self.risk_penalty)

    @property
    def grade(self) -> str:
        s = self.final_score
        if s >= 85:
            return "S"
        if s >= 70:
            return "A"
        if s >= 55:
            return "B"
        if s >= 40:
            return "C"
        return "D"

    @property
    def risk_level(self) -> str:
        p = self.risk_penalty
        if p == 0:
            return "无风险"
        if p >= -15:
            return "低风险"
        if p >= -30:
            return "中风险"
        return "高风险"

    def to_dict(self) -> dict[str, Any]:
        return {
            "specific_score": self.specific_score,
            "specific_items": dict(self.specific_items),
            "common_score": self.common_score,
            "common_items": dict(self.common_items),
            "macd_score": self.macd_score,
            "macd_items": dict(self.macd_items),
            "signal_score": self.signal_score,
            "signal_items": dict(self.signal_items),
            "risk_penalty": self.risk_penalty,
            "risk_items": dict(self.risk_items),
            "bonus_score": self.bonus_score,
            "bonus_items": dict(self.bonus_items),
            "base_score": self.base_score,
            "final_score": self.final_score,
            "grade": self.grade,
            "risk_level": self.risk_level,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ScoreBreakdown:
        return cls(
            specific_score=d.get("specific_score", 0.0),
            specific_items=d.get("specific_items", {}),
            common_score=d.get("common_score", 0.0),
            common_items=d.get("common_items", {}),
            macd_score=d.get("macd_score", 0.0),
            macd_items=d.get("macd_items", {}),
            signal_score=d.get("signal_score", 0.0),
            signal_items=d.get("signal_items", {}),
            risk_penalty=d.get("risk_penalty", 0.0),
            risk_items=d.get("risk_items", {}),
            bonus_score=d.get("bonus_score", 0.0),
            bonus_items=d.get("bonus_items", {}),
        )


@dataclass(frozen=True)
class PatternMatchDetail:
    """单个定式的匹配详情"""

    pattern_type: PatternType
    matched: bool = False
    description: str = ""
    score: float = 0.0
    score_breakdown: ScoreBreakdown | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RiskFilterDetail:
    """单条风险过滤的检测结果"""

    filter_type: RiskFilterType
    triggered: bool = False
    description: str = ""
    penalty: float = 0.0
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

    final_score: float = 0.0
    grade: str = ""
    score_breakdown: ScoreBreakdown | None = None

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
        if self.final_matched:
            grade_str = f" [{self.grade}级]" if self.grade else ""
            score_str = f" {self.final_score:.0f}分" if self.final_score > 0 else ""
            return f"命中: {self.matched_pattern}{score_str}{grade_str}"
        if self.risk_rejected:
            return f"风险过滤: {self.risk_reason}"
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
