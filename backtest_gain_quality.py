"""涨幅质量因子专项回测脚本。

扫描 2025-01-01 ~ 2026-04-30 期间所有砖形图定式命中信号，
按"涨幅质量"评分分组，统计 T+1/T+2/T+3 收益率和胜率，
评估该因子对信号后续表现的区分能力。
"""

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core.data.repository import StockRepository
from core.data.time_index import build_date_index
from core.models.brick_pattern import PatternType
from core.screening.brick_pattern_engine import (
    _calc_indicators,
    check_prerequisites,
    compute_signal_strength_score,
    detect_n_shape_jump,
    detect_sideways_jump,
    detect_uptrend_continue,
)

START_DATE = "2025-01-01"
END_DATE = "2026-04-30"

ENABLED_PATTERNS = (
    PatternType.N_SHAPE_JUMP,
    PatternType.SIDEWAYS_JUMP,
    PatternType.UPTREND_CONTINUE,
)

DETECTORS = {
    PatternType.N_SHAPE_JUMP: detect_n_shape_jump,
    PatternType.SIDEWAYS_JUMP: detect_sideways_jump,
    PatternType.UPTREND_CONTINUE: detect_uptrend_continue,
}


def run_backtest():
    repo = StockRepository(ROOT)
    stock_list = repo.get_stock_list_frame()
    symbols = [str(row["symbol"]).zfill(6) for _, row in stock_list.iterrows()]
    names = {str(row["symbol"]).zfill(6): str(row["name"]) for _, row in stock_list.iterrows()}

    total = len(symbols)
    print(f"共 {total} 只股票，回测区间 {START_DATE} ~ {END_DATE}")

    records = []
    matched_count = 0
    error_count = 0
    start_time = time.time()

    for stock_idx, symbol in enumerate(symbols):
        if (stock_idx + 1) % 200 == 0 or stock_idx == 0:
            elapsed = time.time() - start_time
            print(f"  进度: {stock_idx + 1}/{total}  命中: {matched_count}  耗时: {elapsed:.0f}s")

        try:
            df = repo.get_daily_frame(symbol)
        except Exception:
            error_count += 1
            continue

        if df is None or len(df) < 60:
            continue

        date_index = build_date_index(df)
        close_arr = df["close"].values.astype(float)
        num_rows = len(df)

        try:
            indicators = _calc_indicators(df)
        except Exception:
            error_count += 1
            continue

        for date_str, row_idx in date_index.items():
            if date_str < START_DATE or date_str > END_DATE:
                continue

            passed, _ = check_prerequisites(indicators, row_idx)
            if not passed:
                continue

            for pattern_type in ENABLED_PATTERNS:
                detector = DETECTORS[pattern_type]
                result = detector(indicators, row_idx)
                if not result.matched:
                    continue

                signal_score, signal_items = compute_signal_strength_score(indicators, row_idx)
                gain_quality = signal_items.get("涨幅质量", 0)

                close_signal = float(close_arr[row_idx])

                # T日涨幅(信号日本身的涨幅)
                ret_t0 = np.nan
                if row_idx >= 1:
                    close_prev = float(close_arr[row_idx - 1])
                    if close_prev > 0:
                        ret_t0 = (close_signal - close_prev) / close_prev * 100

                # T+1/T+2/T+3 收益率(相对信号日收盘价)
                ret_t1 = np.nan
                ret_t2 = np.nan
                ret_t3 = np.nan

                if row_idx + 1 < num_rows:
                    ret_t1 = (float(close_arr[row_idx + 1]) - close_signal) / close_signal * 100
                if row_idx + 2 < num_rows:
                    ret_t2 = (float(close_arr[row_idx + 2]) - close_signal) / close_signal * 100
                if row_idx + 3 < num_rows:
                    ret_t3 = (float(close_arr[row_idx + 3]) - close_signal) / close_signal * 100

                records.append({
                    "symbol": symbol,
                    "name": names.get(symbol, ""),
                    "date": date_str,
                    "pattern": pattern_type.value,
                    "gain_quality": gain_quality,
                    "signal_day_change": round(ret_t0, 2) if not np.isnan(ret_t0) else np.nan,
                    "ret_t1": round(ret_t1, 4) if not np.isnan(ret_t1) else np.nan,
                    "ret_t2": round(ret_t2, 4) if not np.isnan(ret_t2) else np.nan,
                    "ret_t3": round(ret_t3, 4) if not np.isnan(ret_t3) else np.nan,
                })
                matched_count += 1

    elapsed = time.time() - start_time
    print(f"\n回测完成: 共命中 {matched_count} 次, 错误 {error_count}, 耗时 {elapsed:.1f}s")

    if not records:
        print("未命中任何定式，无法生成报告。")
        return

    result_df = pd.DataFrame(records)

    output_dir = ROOT / "output"
    output_dir.mkdir(exist_ok=True)
    csv_path = output_dir / "gain_quality_backtest.csv"
    result_df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"明细数据已保存: {csv_path}")

    report = generate_report(result_df)

    report_path = output_dir / "涨幅质量因子分析报告.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"分析报告已保存: {report_path}")


def generate_report(df: pd.DataFrame) -> str:
    """根据回测数据生成涨幅质量因子分析报告。"""
    lines = []
    lines.append("## 砖形图定式评分系统 —— 涨幅质量因子专项分析报告")
    lines.append("")
    lines.append(f"**回测区间**: {START_DATE} ~ {END_DATE}")
    lines.append(f"**总信号数**: {len(df)}")
    lines.append(f"**涉及股票数**: {df['symbol'].nunique()}")
    lines.append("")

    # 涨幅质量分值分布
    lines.append("### 一、涨幅质量分值分布")
    lines.append("")
    score_counts = df["gain_quality"].value_counts().sort_index()
    lines.append("| 涨幅质量分 | 信号数量 | 占比 |")
    lines.append("|:---------:|:-------:|:----:|")
    for score_val, count in score_counts.items():
        pct = count / len(df) * 100
        lines.append(f"| {int(score_val)} | {count} | {pct:.1f}% |")
    lines.append("")

    # 核心分析：按涨幅质量分组统计收益率和胜率
    lines.append("### 二、各分值组 T+1/T+2/T+3 收益率与胜率")
    lines.append("")
    lines.append("> 胜率 = 收益率>0 的信号占比")
    lines.append("")

    score_groups = sorted(df["gain_quality"].unique())
    header = "| 涨幅质量分 | 样本数 | T+1均值 | T+1中位数 | T+1胜率 | T+2均值 | T+2中位数 | T+2胜率 | T+3均值 | T+3中位数 | T+3胜率 |"
    separator = "|:---------:|:-----:|:------:|:--------:|:------:|:------:|:--------:|:------:|:------:|:--------:|:------:|"
    lines.append(header)
    lines.append(separator)

    group_stats = {}
    for score_val in score_groups:
        subset = df[df["gain_quality"] == score_val]
        row_parts = [f"| {int(score_val)}", f"{len(subset)}"]
        stats = {}
        for day_label in ["t1", "t2", "t3"]:
            col = f"ret_{day_label}"
            valid = subset[col].dropna()
            if len(valid) > 0:
                mean_val = valid.mean()
                median_val = valid.median()
                win_rate = (valid > 0).sum() / len(valid) * 100
            else:
                mean_val = median_val = win_rate = 0.0
            row_parts.extend([f"{mean_val:.2f}%", f"{median_val:.2f}%", f"{win_rate:.1f}%"])
            stats[day_label] = {"mean": mean_val, "median": median_val, "win_rate": win_rate, "count": len(valid)}
        row_parts.append("")
        lines.append(" | ".join(row_parts))
        group_stats[int(score_val)] = stats

    lines.append("")

    # 分定式类型分析
    lines.append("### 三、分定式类型的涨幅质量效果")
    lines.append("")
    for pattern_name in df["pattern"].unique():
        pattern_df = df[df["pattern"] == pattern_name]
        lines.append(f"#### {pattern_name}（共{len(pattern_df)}条信号）")
        lines.append("")
        lines.append("| 涨幅质量分 | 样本数 | T+1均值 | T+1胜率 | T+2均值 | T+2胜率 | T+3均值 | T+3胜率 |")
        lines.append("|:---------:|:-----:|:------:|:------:|:------:|:------:|:------:|:------:|")

        for score_val in sorted(pattern_df["gain_quality"].unique()):
            subset = pattern_df[pattern_df["gain_quality"] == score_val]
            row_parts = [f"| {int(score_val)}", f"{len(subset)}"]
            for day_label in ["t1", "t2", "t3"]:
                col = f"ret_{day_label}"
                valid = subset[col].dropna()
                if len(valid) > 0:
                    mean_val = valid.mean()
                    win_rate = (valid > 0).sum() / len(valid) * 100
                else:
                    mean_val = win_rate = 0.0
                row_parts.extend([f"{mean_val:.2f}%", f"{win_rate:.1f}%"])
            row_parts.append("")
            lines.append(" | ".join(row_parts))
        lines.append("")

    # 高分vs低分对比
    lines.append("### 四、高分组(3+5分) vs 低分组(0+1分) 对比")
    lines.append("")
    high_group = df[df["gain_quality"] >= 3]
    low_group = df[df["gain_quality"] <= 1]

    lines.append("| 分组 | 样本数 | T+1均值 | T+1胜率 | T+2均值 | T+2胜率 | T+3均值 | T+3胜率 |")
    lines.append("|:----:|:-----:|:------:|:------:|:------:|:------:|:------:|:------:|")

    for group_name, group_df in [("高分组(3+5)", high_group), ("低分组(0+1)", low_group)]:
        row_parts = [f"| {group_name}", f"{len(group_df)}"]
        for day_label in ["t1", "t2", "t3"]:
            col = f"ret_{day_label}"
            valid = group_df[col].dropna()
            if len(valid) > 0:
                mean_val = valid.mean()
                win_rate = (valid > 0).sum() / len(valid) * 100
            else:
                mean_val = win_rate = 0.0
            row_parts.extend([f"{mean_val:.2f}%", f"{win_rate:.1f}%"])
        row_parts.append("")
        lines.append(" | ".join(row_parts))
    lines.append("")

    # 结论
    lines.append("### 五、结论与建议")
    lines.append("")

    # 自动判断因子有效性
    if group_stats and 5 in group_stats and 0 in group_stats:
        t1_high = group_stats[5]["t1"]["mean"]
        t1_low = group_stats[0]["t1"]["mean"]
        wr_high = group_stats[5]["t1"]["win_rate"]
        wr_low = group_stats[0]["t1"]["win_rate"]
        diff_ret = t1_high - t1_low
        diff_wr = wr_high - wr_low

        if diff_ret > 0.5 and diff_wr > 5:
            verdict = "**有效**：高分组在收益率和胜率上均显著优于低分组"
        elif diff_ret > 0 and diff_wr > 0:
            verdict = "**弱有效**：高分组略优于低分组，但区分度有限"
        elif diff_ret < -0.5 and diff_wr < -5:
            verdict = "**反向有效（负面因子）**：高分组反而不如低分组，因子逻辑可能需要反转"
        else:
            verdict = "**无效**：高低分组无显著差异，该因子对后续收益无区分能力"

        lines.append(f"- **因子有效性判定**: {verdict}")
        lines.append(f"- **T+1收益率差**: 5分组({t1_high:.2f}%) vs 0分组({t1_low:.2f}%), 差值={diff_ret:.2f}%")
        lines.append(f"- **T+1胜率差**: 5分组({wr_high:.1f}%) vs 0分组({wr_low:.1f}%), 差值={diff_wr:.1f}pp")
    else:
        lines.append("- 数据不足，无法自动判断因子有效性。")

    lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    run_backtest()
