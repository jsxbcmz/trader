"""P3-1 OAMV 阶段 + P3-4/5/7 战法加分 单测。

用法：python tests/scoring/test_regime_and_bonus.py
"""

import json
import sys
import tempfile
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from core.data.database import init_databases
from core.models.brick_pattern import PatternType
from core.scoring import RegimeAnalyzer, RegimeRecord, load_regime
from core.scoring.regime import _rolling_slope
from core.screening.brick_pattern.scoring import (
    _diff_dea_cross_age,
    _is_dry_volume,
    _red_green_ratio_ok,
    compute_p3_bonus,
)


# ── P3-1 OAMV 阶段（用真实历史数据） ─────────────────────


def test_regime_user_provided_bull_period():
    """用户提供的多头时段 2025-12-19 ~ 2026-01-28 应主要是 bull。"""
    ra = RegimeAnalyzer.from_root(PROJECT_ROOT)
    series = ra.get_series("2025-12-19", "2026-01-28")
    assert len(series) > 10
    bull_count = sum(1 for r in series if r.smoothed_phase == "bull")
    ratio = bull_count / len(series)
    assert ratio >= 0.7, f"多头时段 bull 比例应 ≥70%，实际 {ratio:.2f}"


def test_regime_user_provided_bear_period():
    """用户提供的空头时段 2025-09-03 ~ 2025-12-12 应主要是 bear。"""
    ra = RegimeAnalyzer.from_root(PROJECT_ROOT)
    series = ra.get_series("2025-09-03", "2025-12-12")
    assert len(series) > 30
    bear_count = sum(1 for r in series if r.smoothed_phase == "bear")
    ratio = bear_count / len(series)
    assert ratio >= 0.7, f"空头时段 bear 比例应 ≥70%，实际 {ratio:.2f}"


def test_regime_smoothing_reduces_switches():
    """平滑应该比原始切换次数少。"""
    ra = RegimeAnalyzer.from_root(PROJECT_ROOT)
    series = ra.get_series("2025-09-01", "2026-05-15")
    raw_sw = sum(1 for i in range(1, len(series)) if series[i].raw_phase != series[i - 1].raw_phase)
    smooth_sw = sum(1 for i in range(1, len(series)) if series[i].smoothed_phase != series[i - 1].smoothed_phase)
    assert smooth_sw <= raw_sw, "平滑切换不应多于原始"


def test_regime_save_and_load_roundtrip():
    """RegimeAnalyzer.save_for_date + load_regime 字段保留。"""
    import shutil
    import pandas as pd
    from core.data.database import MarketDatabase
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        init_databases(root)
        # 将真实 OAMV 数据导入临时数据库
        src = PROJECT_ROOT / "stock_daily_data" / "oamv_930903_CSI.csv"
        oamv_df = pd.read_csv(src)
        oamv_df["date"] = pd.to_datetime(oamv_df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        oamv_df = oamv_df.dropna(subset=["date", "open", "close", "high", "low"])
        from core.data.database import get_market_db
        get_market_db().bulk_upsert_oamv_daily(oamv_df)

        ra = RegimeAnalyzer.from_root(root)
        path = ra.save_for_date("2026-05-15")
        assert path is not None and path.exists()

        loaded = load_regime(root, "2026-05-15")
        assert loaded is not None
        assert loaded.date == "2026-05-15"
        assert loaded.smoothed_phase in ("bull", "bear")
        assert loaded.tempo in ("fast", "slow")
    # 恢复全局数据库到 PROJECT_ROOT，避免污染后续测试
    init_databases(PROJECT_ROOT)


def test_regime_missing_date_returns_none():
    ra = RegimeAnalyzer.from_root(PROJECT_ROOT)
    rec = ra.get_regime("1990-01-01")
    assert rec is None


def test_rolling_slope_basic():
    """_rolling_slope 对单调递增序列应输出正斜率。"""
    values = np.arange(20, dtype=float) + 100  # 100, 101, ..., 119
    slopes = _rolling_slope(values, 5)
    # 前 4 个为 NaN
    assert np.isnan(slopes[3])
    assert np.isfinite(slopes[4])
    assert slopes[-1] > 0


# ── P3-4 红柱 ≥ 绿柱 2/3 ───────────────────────────────


def test_red_green_ratio_all_red():
    """全红应通过（≥ 2/3）。"""
    brick = np.cumsum(np.ones(15))  # 1,2,3,...
    assert _red_green_ratio_ok(brick, 14, window=10)


def test_red_green_ratio_all_green():
    """全绿应不通过。"""
    brick = -np.cumsum(np.ones(15))  # 递减
    assert not _red_green_ratio_ok(brick, 14, window=10)


def test_red_green_ratio_balanced():
    """红 5 + 绿 5 (delta 都是 1) → 1:1 < 2/3 通过（其实是 1.0 ≥ 0.667）。"""
    brick = np.concatenate([np.arange(5), np.arange(5, 0, -1)]).astype(float)
    # delta sequence: +1,+1,+1,+1, -1,-1,-1,-1,-1 (差值 9 个)
    # 但 _red_green_ratio_ok 是 sum 绝对值，所以 4:5 = 0.8 ≥ 2/3 → True
    result = _red_green_ratio_ok(brick, len(brick) - 1, window=10)
    assert result


# ── P3-5 地量 ──────────────────────────────────────────


def test_is_dry_volume_smallest_yes():
    """信号日 volume 是最近 60 天最小 → 应判地量。"""
    volume = np.full(60, 100.0)
    volume[-1] = 10.0  # 当日很小
    assert _is_dry_volume(volume, 59, lookback=60)


def test_is_dry_volume_largest_no():
    """信号日 volume 是最大 → 不是地量。"""
    volume = np.full(60, 100.0)
    volume[-1] = 500.0
    assert not _is_dry_volume(volume, 59, lookback=60)


def test_is_dry_volume_insufficient_data():
    """数据不足 20 → 返回 False。"""
    volume = np.array([100.0, 50.0, 10.0])
    assert not _is_dry_volume(volume, 2, lookback=60)


# ── P3-7 DIFF/DEA 金叉时间 ─────────────────────────────


def test_diff_dea_cross_age_just_today():
    """今天刚金叉 → age=0。"""
    diff = np.array([1.0, 1.0, 2.0])  # 今天上穿
    dea = np.array([1.5, 1.5, 1.5])
    age = _diff_dea_cross_age(diff, dea, 2, lookback=5)
    assert age == 0


def test_diff_dea_cross_age_yesterday():
    diff = np.array([1.0, 2.0, 3.0])  # 昨日上穿
    dea = np.array([1.5, 1.5, 2.5])
    age = _diff_dea_cross_age(diff, dea, 2, lookback=5)
    assert age == 1


def test_diff_dea_cross_age_none():
    """无金叉 → None。"""
    diff = np.array([3.0, 3.0, 3.0])
    dea = np.array([1.5, 1.5, 1.5])
    age = _diff_dea_cross_age(diff, dea, 2, lookback=5)
    assert age is None


# ── compute_p3_bonus 整体 ─────────────────────────────


def test_p3_bonus_returns_dict():
    """compute_p3_bonus 接受标准 indicators，返回 (score, items)。"""
    n = 70
    brick = np.cumsum(np.ones(n))  # 全红 → 红柱比 +2
    volume = np.full(n, 1000.0)
    volume[-1] = 10.0  # 地量 +2
    diff = np.full(n, 1.0)
    dea = np.full(n, 1.5)
    diff[-1] = 2.0  # 今日金叉 +1
    indicators = {
        "brick": brick,
        "volume": volume,
        "macd_diff": diff,
        "macd_dea": dea,
    }
    score, items = compute_p3_bonus(indicators, n - 1, PatternType.N_SHAPE_JUMP)
    assert score == 5  # 2 + 2 + 1
    assert "红柱比2/3" in items
    assert "地量" in items
    assert "DIFF/DEA刚金叉" in items


def test_p3_bonus_no_trigger():
    """无任何 bonus 触发 → score=0 + items 空。"""
    n = 70
    brick = np.cumsum(-np.ones(n))  # 全绿
    volume = np.full(n, 1000.0)
    volume[-1] = 5000.0  # 量大
    diff = np.full(n, 3.0)
    dea = np.full(n, 1.5)
    indicators = {
        "brick": brick,
        "volume": volume,
        "macd_diff": diff,
        "macd_dea": dea,
    }
    score, items = compute_p3_bonus(indicators, n - 1, PatternType.N_SHAPE_JUMP)
    assert score == 0
    assert not items


# ── ScoreBreakdown bonus 字段 ─────────────────────────


def test_score_breakdown_bonus_roundtrip():
    from core.models.brick_pattern import ScoreBreakdown
    bd = ScoreBreakdown(
        specific_score=20.0, common_score=20.0, macd_score=15.0, signal_score=10.0,
        risk_penalty=-5.0, bonus_score=4.0, bonus_items={"红柱比2/3": 2.0, "地量": 2.0},
    )
    # final_score 应该 = base + bonus + risk
    assert bd.base_score == 65.0
    assert bd.final_score == 64.0  # 65 + 4 - 5

    d = bd.to_dict()
    assert d["bonus_score"] == 4.0
    assert d["bonus_items"]["地量"] == 2.0

    bd2 = ScoreBreakdown.from_dict(d)
    assert bd2.bonus_score == 4.0
    assert bd2.bonus_items["红柱比2/3"] == 2.0


def test_score_breakdown_bonus_default_zero():
    """老代码（不传 bonus）仍能工作。"""
    from core.models.brick_pattern import ScoreBreakdown
    bd = ScoreBreakdown(specific_score=20.0)
    assert bd.bonus_score == 0.0
    assert bd.bonus_items == {}


# ── 主入口 ─────────────────────────────────────────────


def main():
    init_databases(PROJECT_ROOT)
    tests = [
        test_regime_user_provided_bull_period,
        test_regime_user_provided_bear_period,
        test_regime_smoothing_reduces_switches,
        test_regime_save_and_load_roundtrip,
        test_regime_missing_date_returns_none,
        test_rolling_slope_basic,
        test_red_green_ratio_all_red,
        test_red_green_ratio_all_green,
        test_red_green_ratio_balanced,
        test_is_dry_volume_smallest_yes,
        test_is_dry_volume_largest_no,
        test_is_dry_volume_insufficient_data,
        test_diff_dea_cross_age_just_today,
        test_diff_dea_cross_age_yesterday,
        test_diff_dea_cross_age_none,
        test_p3_bonus_returns_dict,
        test_p3_bonus_no_trigger,
        test_score_breakdown_bonus_roundtrip,
        test_score_breakdown_bonus_default_zero,
    ]
    passed = failed = 0
    for fn in tests:
        try:
            fn()
            print(f"✅ {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"❌ {fn.__name__}: {e}")
            failed += 1
        except Exception as e:
            import traceback
            print(f"💥 {fn.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n=== {passed} 通过, {failed} 失败 ===")
    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
