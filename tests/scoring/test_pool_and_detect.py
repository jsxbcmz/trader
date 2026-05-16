"""P0-1 主板池 + P0-2c N 型下跌段硬剔除 回归测试。

用法：python tests/scoring/test_pool_and_detect.py
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from core.data.io import load_daily_csv
from core.models.brick_pattern import PatternType
from core.scoring import MainBoardPool
from core.scoring.main_board_pool import _is_main_board, _is_st
from core.screening.brick_pattern.detectors import detect_n_shape_jump
from core.screening.brick_pattern.helpers import _calc_indicators


# ── P0-1 主板池单测 ────────────────────────────────────────


def test_is_main_board_pure_logic():
    assert _is_main_board("000001"), "000001 应判为主板"
    assert _is_main_board("600001"), "600001 应判为主板"
    assert _is_main_board("601318"), "601318 应判为主板"
    assert _is_main_board("605099"), "605099 应判为主板"
    assert not _is_main_board("300001"), "300001 创业板应剔除"
    assert not _is_main_board("301001"), "301001 创业板应剔除"
    assert not _is_main_board("688001"), "688001 科创板应剔除"
    assert not _is_main_board("8x9001"[:6]), "8 开头北交所应剔除"
    assert not _is_main_board("430001"), "4 开头北交所应剔除"
    assert not _is_main_board(""), "空字符串应剔除"
    # 短代码补齐 6 位
    assert _is_main_board("1"), "短代码补齐为 000001 应判为主板"


def test_is_st_pure_logic():
    assert _is_st("*ST中安"), "*ST 应识别"
    assert _is_st("ST 万科"), "ST 应识别"
    assert _is_st("STst"), "包含 st 不区分大小写"
    assert not _is_st("平安银行"), "正常名不应误判"
    assert not _is_st(""), "空字符串不应误判"
    assert not _is_st(None), "None 不应崩溃"  # type: ignore[arg-type]


def test_main_board_pool_returns_valid_subset():
    pool = MainBoardPool.from_root(PROJECT_ROOT)
    candidates = pool.list_active()
    assert len(candidates) > 0, "主板候选不应为空"
    # 所有候选都满足两个条件
    for s in candidates:
        assert _is_main_board(s.symbol), f"{s.symbol} 不应在候选中（非主板）"
        assert not _is_st(s.name), f"{s.symbol} {s.name} 不应在候选中（ST）"


# ── P0-2c N 型下跌段硬剔除回归 ─────────────────────────────


# 设计文档 §四 中列出的 5 个"假命中"案例
N_SHAPE_DECLINE_CASES = [
    ("600519", "2025-11-28", "贵州茅台"),
    ("600519", "2025-09-29", "贵州茅台"),
    ("601318", "2025-09-05", "中国平安"),
    ("000858", "2025-09-08", "五粮液"),
    ("000002", "2025-09-08", "万科A"),
]


def test_n_shape_decline_rejection():
    """5 个文档案例应该全部被 detect_n_shape_jump 剔除。"""
    import pandas as pd
    data_dir = PROJECT_ROOT / "stock_daily_data"
    for sym, date, name in N_SHAPE_DECLINE_CASES:
        df = load_daily_csv(data_dir, sym).sort_values("date").reset_index(drop=True)
        idx = df.index[df["date"] == pd.Timestamp(date)]
        assert len(idx) > 0, f"{sym} 在 {date} 找不到数据"
        i = int(idx[0])
        indicators = _calc_indicators(df)
        result = detect_n_shape_jump(indicators, i)
        assert not result.matched, (
            f"{sym} {date} {name} 应被剔除，但 matched=True；description={result.description}"
        )
        assert "N型下跌段" in result.description, (
            f"{sym} {date} {name} 剔除原因应含'N型下跌段'，实际={result.description}"
        )


def test_n_shape_jump_still_matches_real_case():
    """正例：2026-05-15 双象股份 (002395) 应仍命中 N 型起跳（不是假命中）。

    这是 P0-2 端到端验证中实际跑出来的 #2 候选（79 分），用作回归基准。
    """
    import pandas as pd
    df = load_daily_csv(PROJECT_ROOT / "stock_daily_data", "002395")
    df = df.sort_values("date").reset_index(drop=True)
    idx = df.index[df["date"] == pd.Timestamp("2026-05-15")]
    assert len(idx) > 0, "002395 在 2026-05-15 找不到数据"
    i = int(idx[0])
    indicators = _calc_indicators(df)
    result = detect_n_shape_jump(indicators, i)
    assert result.matched, (
        f"002395 2026-05-15 应命中 N 型起跳（真起跳），description={result.description}"
    )
    assert result.pattern_type == PatternType.N_SHAPE_JUMP


# ── 主入口 ─────────────────────────────────────────────


def main():
    tests = [
        test_is_main_board_pure_logic,
        test_is_st_pure_logic,
        test_main_board_pool_returns_valid_subset,
        test_n_shape_decline_rejection,
        test_n_shape_jump_still_matches_real_case,
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
            print(f"💥 {fn.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n=== {passed} 通过, {failed} 失败 ===")
    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
