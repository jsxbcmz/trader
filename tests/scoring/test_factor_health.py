"""P2-1/P2-2/P2-3 factor_health 单测。

用法：python tests/scoring/test_factor_health.py
"""

import json
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from core.scoring.factor_health import (
    FactorIc,
    aggregate_ic,
    aggregate_monotonicity,
)


# ── aggregate_ic 跨日聚合 ────────────────────────────────


def test_aggregate_ic_single_day():
    daily = [{"factor_a": 0.1, "factor_b": 0.05}]
    result = aggregate_ic(daily)
    assert len(result) == 2
    a = next(r for r in result if r.factor == "factor_a")
    assert a.ic_mean == 0.1
    assert a.ic_std == 0.0  # 单点无标准差
    assert a.n_days == 1


def test_aggregate_ic_multi_day():
    daily = [
        {"factor_a": 0.10, "factor_b": -0.05},
        {"factor_a": 0.20, "factor_b": 0.10},
        {"factor_a": 0.05, "factor_b": -0.02},
    ]
    result = aggregate_ic(daily)
    a = next(r for r in result if r.factor == "factor_a")
    assert abs(a.ic_mean - 0.1167) < 1e-3, f"ic_mean={a.ic_mean}"
    assert a.ic_std > 0
    assert a.ic_ir > 0  # 正方向稳定
    assert a.n_days == 3


def test_aggregate_ic_sorted_by_abs_ic_mean():
    """结果按 |ic_mean| 倒序排列。"""
    daily = [
        {"big_pos": 0.30, "small_pos": 0.02, "big_neg": -0.25},
        {"big_pos": 0.20, "small_pos": 0.01, "big_neg": -0.20},
    ]
    result = aggregate_ic(daily)
    assert result[0].factor == "big_pos"
    assert result[1].factor == "big_neg"
    assert result[2].factor == "small_pos"


def test_aggregate_ic_missing_factor_in_some_days():
    """某因子只在部分天出现 → n_days 反映实际样本天数。"""
    daily = [
        {"factor_a": 0.1, "factor_b": 0.05},
        {"factor_a": 0.2},  # b 缺失
    ]
    result = aggregate_ic(daily)
    b = next(r for r in result if r.factor == "factor_b")
    assert b.n_days == 1


# ── aggregate_monotonicity 单调性 ──────────────────────


def test_monotonicity_strictly_increasing():
    """4 个分箱严格递增 → is_monotonic=True。"""
    daily = [
        {"factor_a": {0: -0.02, 1: 0.0, 2: 0.01, 3: 0.03}},
        {"factor_a": {0: -0.01, 1: 0.01, 2: 0.02, 3: 0.04}},
    ]
    result = aggregate_monotonicity(daily)
    info = result["factor_a"]
    assert info["is_monotonic"] is True
    assert info["spread"] > 0


def test_monotonicity_not_increasing():
    daily = [
        {"factor_a": {0: 0.05, 1: -0.02, 2: 0.01, 3: -0.03}},
    ]
    result = aggregate_monotonicity(daily)
    info = result["factor_a"]
    assert info["is_monotonic"] is False


def test_monotonicity_skips_too_few_bins():
    """分箱不足 3 个 → 不出现在结果里。"""
    daily = [
        {"factor_a": {0: 0.01, 1: 0.02}},  # 只 2 个箱
    ]
    result = aggregate_monotonicity(daily)
    assert "factor_a" not in result


# ── FactorIc 数据类 ────────────────────────────────────


def test_factor_ic_fields():
    f = FactorIc(factor="x", ic_mean=0.1, ic_std=0.02, ic_ir=5.0, t_stat=10.0, n_days=10)
    assert f.factor == "x"
    assert f.is_monotonic is None  # 默认未设置


# ── 月报 JSON 往返 ────────────────────────────────────


def test_monthly_report_json_roundtrip():
    """generate_monthly_report 落盘的 JSON 能被 load 回读，字段保留。"""
    from core.scoring.factor_health import load_monthly_report

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        # 构造一个最小报告（不跑真实 generate）
        sample = {
            "year_month": "2026-05",
            "date_range": ["2026-05-01", "2026-05-15"],
            "n_days": 5,
            "k": 20,
            "topk_summary": {
                "t1": {"avg_return": 0.02, "win_rate": 0.6, "n_samples": 100},
            },
            "factor_ic": {
                "t1": [
                    {"factor": "fa", "ic_mean": 0.1, "ic_std": 0.05,
                     "ic_ir": 2.0, "t_stat": 4.5, "n_days": 5},
                ],
            },
            "monotonicity": {"t1": {}},
            "alerts": [{"factor": "fa", "type": "无效因子", "detail": "test"}],
        }
        out_dir = root / "output" / "scoring_factor_health"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "2026-05.json"
        with path.open("w", encoding="utf-8") as f:
            json.dump(sample, f, ensure_ascii=False, indent=2)

        loaded = load_monthly_report(root, "2026-05")
        assert loaded["year_month"] == "2026-05"
        assert loaded["n_days"] == 5
        assert loaded["factor_ic"]["t1"][0]["factor"] == "fa"
        assert loaded["alerts"][0]["type"] == "无效因子"


def test_load_monthly_report_missing_returns_empty():
    with tempfile.TemporaryDirectory() as tmpdir:
        from core.scoring.factor_health import load_monthly_report
        assert load_monthly_report(Path(tmpdir), "1990-01") == {}


# ── 主入口 ─────────────────────────────────────────────


def main():
    tests = [
        test_aggregate_ic_single_day,
        test_aggregate_ic_multi_day,
        test_aggregate_ic_sorted_by_abs_ic_mean,
        test_aggregate_ic_missing_factor_in_some_days,
        test_monotonicity_strictly_increasing,
        test_monotonicity_not_increasing,
        test_monotonicity_skips_too_few_bins,
        test_factor_ic_fields,
        test_monthly_report_json_roundtrip,
        test_load_monthly_report_missing_returns_empty,
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
