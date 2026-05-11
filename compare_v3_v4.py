"""V3 vs V4 评分引擎回测对比脚本。

策略：V3数据从已有的 factor_backtest_raw.csv 读取，V4用当前引擎重新评分。
对比各评分等级的 T+1/T+2/T+3 胜率和收益率变化。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core.data.repository import StockRepository
from core.data.time_index import build_date_index
from core.models.brick_pattern import PatternType, ScoreBreakdown
from core.screening.brick_pattern_engine import (
    _calc_indicators,
    check_prerequisites,
    compute_common_quality_score,
    compute_macd_auxiliary_score,
    compute_risk_penalty,
    compute_signal_strength_score,
    detect_n_shape_jump,
    detect_sideways_jump,
    detect_uptrend_continue,
)

START_DATE = "2025-01-01"
END_DATE = "2026-04-30"
FUTURE_DAYS = 3

PATTERN_MAP = {
    "N型起跳": PatternType.N_SHAPE_JUMP,
    "横盘起跳": PatternType.SIDEWAYS_JUMP,
    "上升波段延续": PatternType.UPTREND_CONTINUE,
}

DETECTORS = {
    PatternType.N_SHAPE_JUMP: detect_n_shape_jump,
    PatternType.SIDEWAYS_JUMP: detect_sideways_jump,
    PatternType.UPTREND_CONTINUE: detect_uptrend_continue,
}


def collect_v4_signals():
    """用V4引擎重新扫描，收集评分数据。"""
    repo = StockRepository(ROOT)
    stock_list = repo.get_stock_list_frame()
    symbols = [str(row["symbol"]).zfill(6) for _, row in stock_list.iterrows()]

    total = len(symbols)
    print(f"V4重评: 共 {total} 只股票")

    records = []
    matched_count = 0
    start_time = time.time()

    for idx, symbol in enumerate(symbols):
        if (idx + 1) % 200 == 0 or idx == 0:
            elapsed = time.time() - start_time
            print(f"  进度: {idx + 1}/{total}  命中: {matched_count}  耗时: {elapsed:.0f}s")

        try:
            df = repo.get_daily_frame(symbol)
        except Exception:
            continue
        if df is None or len(df) < 60:
            continue

        date_index = build_date_index(df)
        close_arr = df["close"].values.astype(float)
        n_rows = len(df)

        try:
            indicators = _calc_indicators(df)
        except Exception:
            continue

        for date_str, row_idx in date_index.items():
            if date_str < START_DATE or date_str > END_DATE:
                continue
            passed, _ = check_prerequisites(indicators, row_idx)
            if not passed:
                continue

            for pattern_type in PATTERN_MAP.values():
                result = DETECTORS[pattern_type](indicators, row_idx)
                if not result.matched:
                    continue

                common_score, _ = compute_common_quality_score(indicators, row_idx, pattern_type)
                macd_score, _ = compute_macd_auxiliary_score(indicators, row_idx, pattern_type)
                risk_penalty, _, _ = compute_risk_penalty(indicators, row_idx, pattern_type)
                signal_score, _ = compute_signal_strength_score(indicators, row_idx)

                breakdown = ScoreBreakdown(
                    specific_score=result.score,
                    common_score=common_score,
                    macd_score=macd_score,
                    signal_score=signal_score,
                    risk_penalty=risk_penalty,
                )

                close_t = float(close_arr[row_idx])
                future_returns = {}
                for day_offset in range(1, FUTURE_DAYS + 1):
                    future_idx = row_idx + day_offset
                    if future_idx < n_rows:
                        future_returns[f"ret_t{day_offset}"] = round(
                            (float(close_arr[future_idx]) - close_t) / close_t * 100, 4)
                    else:
                        future_returns[f"ret_t{day_offset}"] = np.nan

                records.append({
                    "symbol": symbol,
                    "date": date_str,
                    "pattern": pattern_type.value,
                    "v4_score": round(breakdown.final_score, 1),
                    "v4_grade": breakdown.grade,
                    **future_returns,
                })
                matched_count += 1

    print(f"V4评分完成: {matched_count} 条, 耗时 {time.time()-start_time:.1f}s")
    return pd.DataFrame(records)


def print_comparison(v3: pd.DataFrame, v4: pd.DataFrame):
    """打印V3 vs V4对比。"""
    # 按 symbol+date+pattern 合并
    merged = v3.merge(v4, on=["symbol", "date", "pattern"], suffixes=("_v3", "_v4"), how="inner")
    print(f"\n匹配信号数: V3={len(v3)}, V4={len(v4)}, 交集={len(merged)}")

    # 使用 v3 的收益率（与 v4 的应该一致）
    for col in ["ret_t1", "ret_t2", "ret_t3"]:
        if f"{col}_v3" in merged.columns:
            merged[col] = merged[f"{col}_v3"]

    lines = []
    lines.append("=" * 80)
    lines.append("V3 vs V4 评分引擎回测对比报告")
    lines.append("=" * 80)
    lines.append(f"回测区间: {START_DATE} ~ {END_DATE}")
    lines.append(f"对比信号数: {len(merged)}")
    lines.append(f"V3 均分: {merged['v3_score'].mean():.1f}  V4 均分: {merged['v4_score'].mean():.1f}")
    lines.append(f"V3 S+A占比: {(merged['v3_grade'].isin(['S','A'])).mean()*100:.1f}%  "
                 f"V4 S+A占比: {(merged['v4_grade'].isin(['S','A'])).mean()*100:.1f}%")

    # 各等级对比
    for version, grade_col, score_col in [("V3", "v3_grade", "v3_score"), ("V4", "v4_grade", "v4_score")]:
        lines.append(f"\n--- {version} 各等级表现 ---")
        lines.append(f"{'等级':>4} {'信号数':>8} {'占比':>7} {'T+1均值':>10} {'T+1胜率':>8} {'T+2均值':>10} {'T+2胜率':>8} {'T+3均值':>10} {'T+3胜率':>8}")
        for grade in ["S", "A", "B", "C", "D"]:
            gdf = merged[merged[grade_col] == grade]
            if len(gdf) == 0:
                continue
            pct = len(gdf) / len(merged) * 100
            row_parts = [f"{grade:>4}", f"{len(gdf):>8}", f"{pct:>6.1f}%"]
            for col in ["ret_t1", "ret_t2", "ret_t3"]:
                ret = gdf[col].dropna()
                if len(ret) > 0:
                    row_parts.append(f"{ret.mean():>+9.3f}%")
                    row_parts.append(f"{(ret>0).mean()*100:>7.1f}%")
                else:
                    row_parts.extend(["     -   ", "     -  "])
            lines.append(" ".join(row_parts))

    # 按定式对比
    for pattern_name in ["N型起跳", "横盘起跳", "上升波段延续"]:
        pdf = merged[merged["pattern"] == pattern_name]
        if len(pdf) == 0:
            continue
        lines.append(f"\n{'='*60}")
        lines.append(f"定式: {pattern_name} ({len(pdf)} 条)")
        for version, grade_col in [("V3", "v3_grade"), ("V4", "v4_grade")]:
            lines.append(f"  {version}:")
            for grade in ["S", "A", "B", "C", "D"]:
                gdf = pdf[pdf[grade_col] == grade]
                if len(gdf) == 0:
                    continue
                t1 = gdf["ret_t1"].dropna()
                t3 = gdf["ret_t3"].dropna()
                lines.append(f"    {grade}: {len(gdf):>5}条  "
                             f"T+1={t1.mean():>+.3f}%/{(t1>0).mean()*100:.1f}%  "
                             f"T+3={t3.mean():>+.3f}%/{(t3>0).mean()*100:.1f}%")

    # 等级变动
    grade_rank = {"S": 5, "A": 4, "B": 3, "C": 2, "D": 1}
    merged["v3_rank"] = merged["v3_grade"].map(grade_rank)
    merged["v4_rank"] = merged["v4_grade"].map(grade_rank)
    merged["rank_change"] = merged["v4_rank"] - merged["v3_rank"]

    lines.append(f"\n{'='*60}")
    lines.append("等级变动分析")
    for label, cond in [("升级(V4>V3)", merged["rank_change"] > 0),
                        ("不变", merged["rank_change"] == 0),
                        ("降级(V4<V3)", merged["rank_change"] < 0)]:
        subset = merged[cond]
        if len(subset) == 0:
            continue
        t1 = subset["ret_t1"].dropna()
        t3 = subset["ret_t3"].dropna()
        lines.append(f"  {label}: {len(subset):>6}条({len(subset)/len(merged)*100:.1f}%)  "
                     f"T+1={t1.mean():>+.3f}%/{(t1>0).mean()*100:.1f}%  "
                     f"T+3={t3.mean():>+.3f}%/{(t3>0).mean()*100:.1f}%")

    # 核心对比: S+A 和 C+D
    lines.append(f"\n{'='*60}")
    lines.append("V4优化效果")
    for label, grades in [("S+A(高质量)", ["S","A"]), ("B(中等)", ["B"]), ("C+D(低质量)", ["C","D"])]:
        v3_sub = merged[merged["v3_grade"].isin(grades)]
        v4_sub = merged[merged["v4_grade"].isin(grades)]
        if len(v3_sub) == 0 or len(v4_sub) == 0:
            continue
        v3t1 = v3_sub["ret_t1"].dropna(); v4t1 = v4_sub["ret_t1"].dropna()
        v3t3 = v3_sub["ret_t3"].dropna(); v4t3 = v4_sub["ret_t3"].dropna()

        lines.append(f"\n  {label}:")
        lines.append(f"    V3: {len(v3_sub):>5}条  T+1={v3t1.mean():>+.3f}%/{(v3t1>0).mean()*100:.1f}%  T+3={v3t3.mean():>+.3f}%/{(v3t3>0).mean()*100:.1f}%")
        lines.append(f"    V4: {len(v4_sub):>5}条  T+1={v4t1.mean():>+.3f}%/{(v4t1>0).mean()*100:.1f}%  T+3={v4t3.mean():>+.3f}%/{(v4t3>0).mean()*100:.1f}%")
        t1d = v4t1.mean() - v3t1.mean()
        t3d = v4t3.mean() - v3t3.mean()
        wr1d = (v4t1>0).mean()*100 - (v3t1>0).mean()*100
        wr3d = (v4t3>0).mean()*100 - (v3t3>0).mean()*100
        lines.append(f"    变化: T+1收益{t1d:>+.3f}% 胜率{wr1d:>+.1f}pp | T+3收益{t3d:>+.3f}% 胜率{wr3d:>+.1f}pp")

    output = "\n".join(lines)
    print(output)
    return output, merged


def main():
    print("=" * 60)
    print("V3 vs V4 评分引擎回测对比")
    print("=" * 60)

    # 读取V3数据
    v3_csv = ROOT / "output" / "factor_backtest_raw.csv"
    if not v3_csv.exists():
        print(f"错误: V3数据文件不存在 {v3_csv}")
        return
    v3_df = pd.read_csv(v3_csv)
    v3_df["symbol"] = v3_df["symbol"].astype(str).str.zfill(6)
    v3_renamed = v3_df[["symbol", "date", "pattern", "final_score", "grade",
                         "ret_t1", "ret_t2", "ret_t3"]].rename(
        columns={"final_score": "v3_score", "grade": "v3_grade"})
    print(f"V3数据: {len(v3_renamed)} 条")

    # 收集V4数据
    v4_df = collect_v4_signals()
    if v4_df.empty:
        print("V4未命中任何信号")
        return

    # 保存V4原始数据
    v4_csv = ROOT / "output" / "v4_backtest_raw.csv"
    v4_df.to_csv(v4_csv, index=False, encoding="utf-8-sig")
    print(f"V4数据已保存: {v4_csv}")

    # 对比
    report_text, merged = print_comparison(v3_renamed, v4_df)

    # 保存合并数据
    merge_csv = ROOT / "output" / "v3_v4_compare.csv"
    merged.to_csv(merge_csv, index=False, encoding="utf-8-sig")
    print(f"\n对比数据已保存: {merge_csv}")


if __name__ == "__main__":
    main()
