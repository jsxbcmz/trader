"""P1-1 截面分位计算 + P1-2 scoring.py 分位查表 单测。

用法：python tests/scoring/test_cross_section.py
"""

import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from core.models.brick_pattern import PatternType
from core.scoring import get_symbol_pcts, load_cross_section
from core.scoring.cross_section import CS_COLUMNS, _cs_path
from core.screening.brick_pattern.scoring import _pct_to_score, compute_common_quality_score


# ── P1-2 _pct_to_score 分箱单测 ────────────────────────────


def test_pct_to_score_bins():
    # 5 档：≥0.95 满分 / 0.80~0.95 高(75%) / 0.50~0.80 中(50%) / 0.20~0.50 低(25%) / <0.20 零
    # 8 分制
    assert _pct_to_score(0.99, 8) == 8, "≥0.95 应满分"
    assert _pct_to_score(0.95, 8) == 8, "0.95 边界应满分"
    assert _pct_to_score(0.85, 8) == 6, "0.85 应 75%（8*0.75=6）"
    assert _pct_to_score(0.80, 8) == 6
    assert _pct_to_score(0.65, 8) == 4, "0.65 应 50%（8*0.5=4）"
    assert _pct_to_score(0.50, 8) == 4
    assert _pct_to_score(0.30, 8) == 2, "0.30 应 25%（8*0.25=2）"
    assert _pct_to_score(0.20, 8) == 2
    assert _pct_to_score(0.10, 8) == 0, "<0.20 应为 0"
    # 3 分制（翻红力度比/短趋斜率）
    assert _pct_to_score(0.99, 3) == 3
    assert _pct_to_score(0.85, 3) == 2  # round(2.25) = 2
    assert _pct_to_score(0.65, 3) == 2  # round(1.5) = 2
    assert _pct_to_score(0.30, 3) == 1  # round(0.75) = 1
    assert _pct_to_score(0.10, 3) == 0


# ── P1-2 compute_common_quality_score 走分位查表（cs_pcts 非 None） ──


def _fake_indicators(length: int = 30):
    """构造一组最小可用的 indicators，配合 cs_pcts 走分位查表。"""
    close = np.full(length, 10.0)
    close[-1] = 10.5  # 当日上涨
    brick = np.arange(length, dtype=float) * 1.0  # 单调上升
    short_trend = np.arange(length, dtype=float) * 0.5
    long_short = np.full(length, 5.0)
    return {
        "close": close,
        "open": np.full(length, 10.0),
        "high": np.full(length, 10.6),
        "low": np.full(length, 9.9),
        "brick": brick,
        "short_trend": short_trend,
        "long_short": long_short,
        "ma14": np.full(length, 9.9),
        "ma28": np.full(length, 9.8),
        "ma57": np.full(length, 9.7),
        "ma114": np.full(length, 9.6),
    }


def test_common_quality_uses_cs_pcts_when_provided():
    """传 cs_pcts 时，3 个待归一化因子走分位查表（绝对阈值不再生效）。"""
    indicators = _fake_indicators()
    cs_pcts = {
        "day_change_pct": 0.99,            # 满分 8
        "force_ratio_pct": 0.99,           # 满分 3
        "short_trend_slope_pct": 0.99,     # 满分 3
    }
    score, items = compute_common_quality_score(
        indicators, len(indicators["close"]) - 1, PatternType.N_SHAPE_JUMP,
        cs_pcts=cs_pcts,
    )
    assert items["信号日涨幅"] == 8, f"应取分位满分 8，实际 {items['信号日涨幅']}"
    assert items["翻红力度比"] == 3, f"应取分位满分 3，实际 {items['翻红力度比']}"
    assert items["短趋斜率"] == 3, f"应取分位满分 3，实际 {items['短趋斜率']}"


def test_common_quality_fallback_to_absolute_when_cs_pcts_none():
    """不传 cs_pcts 时，3 个因子走原绝对阈值逻辑（向后兼容）。"""
    indicators = _fake_indicators()
    # 当日涨幅 ≈ 5%（close[-1]=10.5, close[-2]=10.0），N 型起跳应得 8 分
    score, items = compute_common_quality_score(
        indicators, len(indicators["close"]) - 1, PatternType.N_SHAPE_JUMP,
        cs_pcts=None,
    )
    # 走旧逻辑：N 型 + 5% 涨幅 → 8 分
    assert items["信号日涨幅"] == 8, f"绝对阈值应 8 分，实际 {items['信号日涨幅']}"


def test_common_quality_low_pct_low_score():
    """分位低（< 0.20）应得 0 分。"""
    indicators = _fake_indicators()
    cs_pcts = {
        "day_change_pct": 0.05,
        "force_ratio_pct": 0.05,
        "short_trend_slope_pct": 0.05,
    }
    _, items = compute_common_quality_score(
        indicators, len(indicators["close"]) - 1, PatternType.N_SHAPE_JUMP,
        cs_pcts=cs_pcts,
    )
    assert items["信号日涨幅"] == 0
    assert items["翻红力度比"] == 0
    assert items["短趋斜率"] == 0


# ── P1-1 cross_section CSV 往返 ────────────────────────────


def test_cross_section_csv_roundtrip():
    """save + load 后 DataFrame 字段与值保持一致。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        df = pd.DataFrame([
            {"symbol": "000001", "day_change": 5.5, "day_change_pct": 0.95,
             "force_ratio": 2.3, "force_ratio_pct": 0.88,
             "short_trend_slope": 0.42, "short_trend_slope_pct": 0.78},
            {"symbol": "600519", "day_change": -1.2, "day_change_pct": 0.35,
             "force_ratio": 0.8, "force_ratio_pct": 0.55,
             "short_trend_slope": 0.10, "short_trend_slope_pct": 0.50},
        ])[list(CS_COLUMNS)]

        path = _cs_path(root, "2026-05-15")
        df.to_csv(path, index=False, encoding="utf-8")

        loaded = load_cross_section(root, "2026-05-15")
        assert len(loaded) == 2
        assert list(loaded.columns) == list(CS_COLUMNS), "列顺序应一致"
        # symbol 保持字符串
        assert loaded.iloc[0]["symbol"] == "000001"
        # 数值精度
        assert abs(loaded.iloc[0]["day_change"] - 5.5) < 1e-9
        assert abs(loaded.iloc[0]["day_change_pct"] - 0.95) < 1e-9


def test_get_symbol_pcts():
    df = pd.DataFrame([
        {"symbol": "000001", "day_change": 5.5, "day_change_pct": 0.95,
         "force_ratio": 2.3, "force_ratio_pct": 0.88,
         "short_trend_slope": 0.42, "short_trend_slope_pct": 0.78},
    ])
    pcts = get_symbol_pcts(df, "000001")
    assert pcts is not None
    assert pcts["day_change_pct"] == 0.95
    assert pcts["force_ratio_pct"] == 0.88
    assert pcts["short_trend_slope_pct"] == 0.78
    # 不存在的票
    assert get_symbol_pcts(df, "999999") is None
    # 短代码自动补齐
    pcts = get_symbol_pcts(df, "1")  # 补齐为 000001
    assert pcts is not None and pcts["day_change_pct"] == 0.95


def test_load_cross_section_missing_returns_empty():
    with tempfile.TemporaryDirectory() as tmpdir:
        df = load_cross_section(Path(tmpdir), "1990-01-01")
        assert df.empty
        assert list(df.columns) == list(CS_COLUMNS)


# ── 主入口 ─────────────────────────────────────────────


def main():
    tests = [
        test_pct_to_score_bins,
        test_common_quality_uses_cs_pcts_when_provided,
        test_common_quality_fallback_to_absolute_when_cs_pcts_none,
        test_common_quality_low_pct_low_score,
        test_cross_section_csv_roundtrip,
        test_get_symbol_pcts,
        test_load_cross_section_missing_returns_empty,
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
