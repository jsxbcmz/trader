"""P0-3/4 storage JSON 往返 + P0-5 outcomes 增量更新 / CSV 往返测试。

用法：python tests/scoring/test_storage_and_outcomes.py
"""

import json
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from core.models.brick_pattern import BrickPatternMatch, PatternType, ScoreBreakdown
from core.scoring import (
    OutcomeRecord,
    load_outcomes,
    load_scoring_daily,
    load_scoring_picks,
    save_scoring_daily,
    save_scoring_picks,
)
from core.scoring.outcomes import _load_outcomes, _save_outcomes


# ── 测试 fixture ──────────────────────────────────────────


def make_match(symbol: str, total: float, pattern: str = "N型起跳", date: str = "2026-05-15") -> BrickPatternMatch:
    """构造一个最小可用的"已命中"BrickPatternMatch。"""
    bd = ScoreBreakdown(
        specific_score=25.0, specific_items={"超卖深度": 12.0, "回调充分度": 8.0},
        common_score=20.0, common_items={"K线形态": 10.0, "信号日涨幅": 8.0},
        macd_score=20.0, macd_items={"MACD加分": 5.0},
        signal_score=15.0, signal_items={"信号强度": 15.0},
        risk_penalty=0.0, risk_items={},
    )
    return BrickPatternMatch(
        symbol=symbol,
        name=f"测试_{symbol}",
        target_date=date,
        actual_date=date,
        prerequisite_passed=True,
        prerequisite_detail="前提通过",
        final_matched=True,
        matched_pattern=pattern,
        final_score=total,
        grade="A" if total >= 70 else "B",
        score_breakdown=bd,
    )


# ── P0-3 scoring_daily JSON 往返 ───────────────────────────


def test_scoring_daily_roundtrip():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        date = "2026-05-15"
        matches = [
            make_match("000001", 85.0),
            make_match("000002", 78.0, pattern="横盘起跳"),
            make_match("000003", 0.0),  # 故意未命中（final_matched=False 应被过滤）
        ]
        # 把第 3 个改成未命中
        matches[2] = BrickPatternMatch(
            symbol="000003", final_matched=False, error="未命中三定式",
        )

        path = save_scoring_daily(root, date, matches)
        assert path.exists(), f"daily 文件未生成：{path}"

        records = load_scoring_daily(root, date)
        assert len(records) == 2, f"只应存命中票（2 条），实际 {len(records)}"
        # 按 total_score 倒序
        assert records[0].total_score == 85.0, "应按总分倒序"
        assert records[0].symbol == "000001"
        assert records[1].total_score == 78.0
        # 子项明细完整
        assert "超卖深度" in records[0].items
        assert records[0].items["超卖深度"] == 12.0


def test_scoring_daily_load_missing_returns_empty():
    """读不存在的日期返回空列表。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        records = load_scoring_daily(Path(tmpdir), "1990-01-01")
        assert records == []


# ── P0-4 scoring_picks JSON 往返 + Top K ────────────────────


def test_scoring_picks_top_k_and_sort():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        date = "2026-05-15"
        # 25 只命中，K=10 应只取前 10
        matches = [make_match(f"{i:06d}", float(50 + i)) for i in range(25)]
        path = save_scoring_picks(root, date, matches, k=10, regime="多头")
        assert path.exists()

        data = load_scoring_picks(root, date)
        assert data["date"] == date
        assert data["regime"] == "多头"
        assert data["k"] == 10
        picks = data["picks"]
        assert len(picks) == 10, f"Top K 应为 10 条，实际 {len(picks)}"
        # 排序：rank=1 应是分最高的（"000024" 总分 74）
        assert picks[0]["rank"] == 1
        assert picks[0]["symbol"] == "000024"
        assert picks[0]["total"] == 74.0
        # breakdown 中文键存在
        assert "定式专属" in picks[0]["breakdown"]
        assert "子项明细" in picks[0]["breakdown"]


def test_scoring_picks_excludes_non_matched():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        matches = [
            make_match("000001", 80.0),
            BrickPatternMatch(symbol="000002", final_matched=False, error="未命中"),
        ]
        save_scoring_picks(root, "2026-05-15", matches, k=20)
        data = load_scoring_picks(root, "2026-05-15")
        assert len(data["picks"]) == 1, "未命中票应被排除"


# ── P0-5 outcomes CSV 往返 + None 值处理 ───────────────────


def test_outcomes_roundtrip_with_none():
    from core.data.database import init_databases
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        init_databases(root)
        date = "2026-05-15"
        records = {
            "000001": OutcomeRecord(
                symbol="000001", score_date=date,
                t1_return=0.05, t1_is_green=False,
                t2_return=-0.02, t2_is_green=True,
                t3_return=None, t3_is_green=None,
            ),
            "000002": OutcomeRecord(
                symbol="000002", score_date=date,
                t1_return=None, t1_is_green=None,
                t2_return=None, t2_is_green=None,
                t3_return=None, t3_is_green=None,
            ),
        }
        _save_outcomes(root, date, records)

        loaded = _load_outcomes(root, date)
        assert len(loaded) == 2
        # 第一条：t1/t2 有值，t3 是 None
        r1 = loaded["000001"]
        assert r1.t1_return == 0.05
        assert r1.t1_is_green is False
        assert r1.t2_return == -0.02
        assert r1.t2_is_green is True
        assert r1.t3_return is None, f"t3_return 应为 None，实际 {r1.t3_return}"
        assert r1.t3_is_green is None
        # 第二条：全是 None
        r2 = loaded["000002"]
        assert all(v is None for v in [
            r2.t1_return, r2.t1_is_green,
            r2.t2_return, r2.t2_is_green,
            r2.t3_return, r2.t3_is_green,
        ])


def test_outcomes_incremental_update():
    """模拟 T+1/T+2/T+3 三天分别填充对应列的场景。"""
    from core.data.database import init_databases
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        init_databases(root)
        date = "2026-05-12"

        # 第 1 天（T+1 收盘后）：只写 t1
        records = {
            "000001": OutcomeRecord(
                symbol="000001", score_date=date,
                t1_return=0.03, t1_is_green=False,
            ),
        }
        _save_outcomes(root, date, records)

        # 第 2 天（T+2 收盘后）：增量加 t2 列
        loaded = _load_outcomes(root, date)
        loaded["000001"].t2_return = -0.01
        loaded["000001"].t2_is_green = True
        _save_outcomes(root, date, loaded)

        # 验证 t1 列仍在
        reloaded = _load_outcomes(root, date)
        r = reloaded["000001"]
        assert r.t1_return == 0.03, "增量更新不应丢失 t1"
        assert r.t1_is_green is False
        assert r.t2_return == -0.01
        assert r.t2_is_green is True
        assert r.t3_return is None

        # 第 3 天：补 t3
        r.t3_return = 0.07
        r.t3_is_green = False
        _save_outcomes(root, date, reloaded)

        final = _load_outcomes(root, date)
        f = final["000001"]
        assert f.t1_return == 0.03
        assert f.t2_return == -0.01
        assert f.t3_return == 0.07
        assert f.t3_is_green is False


def test_outcomes_db_roundtrip():
    """检查 outcomes 数据库写入和读取的完整性。"""
    from core.data.database import init_databases, get_scoring_db
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        init_databases(root)
        records = {
            "000001": OutcomeRecord(symbol="000001", score_date="2026-05-15",
                                    t1_return=0.05, t1_is_green=True),
        }
        _save_outcomes(root, "2026-05-15", records)
        loaded = _load_outcomes(root, "2026-05-15")
        assert len(loaded) == 1
        r = loaded["000001"]
        assert r.t1_return == 0.05
        assert r.t1_is_green is True


# ── 主入口 ─────────────────────────────────────────────


def main():
    tests = [
        test_scoring_daily_roundtrip,
        test_scoring_daily_load_missing_returns_empty,
        test_scoring_picks_top_k_and_sort,
        test_scoring_picks_excludes_non_matched,
        test_outcomes_roundtrip_with_none,
        test_outcomes_incremental_update,
        test_outcomes_db_roundtrip,
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
