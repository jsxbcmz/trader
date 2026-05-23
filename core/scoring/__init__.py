"""主板评分系统（V4 砖形图评分的全主板扩展）。"""

from core.scoring.cross_section import (
    CrossSectionStats,
    get_symbol_pcts,
    load_cross_section,
)
from core.scoring.engine import MainBoardScoringEngine
from core.scoring.factor_health import FactorHealth, FactorIc, load_monthly_report
from core.scoring.regime import RegimeAnalyzer, RegimeRecord, load_regime
from core.scoring.main_board_pool import MainBoardPool
from core.scoring.outcomes import OutcomesFiller, OutcomeRecord, load_outcomes
from core.scoring.storage import (
    ScoringRecord,
    load_scoring_daily,
    load_scoring_picks,
    save_scoring_daily,
    save_scoring_picks,
)

__all__ = [
    "CrossSectionStats",
    "FactorHealth",
    "FactorIc",
    "RegimeAnalyzer",
    "RegimeRecord",
    "load_monthly_report",
    "load_regime",
    "MainBoardPool",
    "MainBoardScoringEngine",
    "OutcomeRecord",
    "OutcomesFiller",
    "ScoringRecord",
    "get_symbol_pcts",
    "load_cross_section",
    "load_outcomes",
    "load_scoring_daily",
    "load_scoring_picks",
    "save_scoring_daily",
    "save_scoring_picks",
]
