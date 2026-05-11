"""砖形图评分系统 — 单因子独立回测脚本。

遍历所有股票，在 2025-01-01 ~ 2026-04-30 期间找出所有命中定式的信号，
记录每个因子的原始值和得分，统计各因子不同分值区间对 T+1/T+2/T+3 收益率/胜率的影响。
最终生成 Markdown 分析报告。
"""

from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
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
FUTURE_DAYS = 3  # 观察 T+1, T+2, T+3

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


def collect_signals():
    """扫描所有股票，收集命中信号的全量因子数据。"""
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

    for idx, symbol in enumerate(symbols):
        if (idx + 1) % 200 == 0 or idx == 0:
            elapsed = time.time() - start_time
            print(f"  进度: {idx + 1}/{total}  命中: {matched_count}  耗时: {elapsed:.0f}s")

        try:
            df = repo.get_daily_frame(symbol)
        except Exception:
            error_count += 1
            continue

        if df is None or len(df) < 60:
            continue

        date_index = build_date_index(df)
        close_arr = df["close"].values.astype(float)
        n_rows = len(df)

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

                specific_score = result.score
                specific_items = result.extra.get("specific_items", {})

                common_score, common_items = compute_common_quality_score(
                    indicators, row_idx, pattern_type,
                )
                macd_score, macd_items = compute_macd_auxiliary_score(
                    indicators, row_idx, pattern_type,
                )
                risk_penalty, risk_items, _ = compute_risk_penalty(
                    indicators, row_idx, pattern_type,
                )
                signal_score, signal_items = compute_signal_strength_score(
                    indicators, row_idx,
                )

                breakdown = ScoreBreakdown(
                    specific_score=specific_score,
                    specific_items=specific_items,
                    common_score=common_score,
                    common_items=common_items,
                    macd_score=macd_score,
                    macd_items=macd_items,
                    signal_score=signal_score,
                    signal_items=signal_items,
                    risk_penalty=risk_penalty,
                    risk_items=risk_items,
                )

                close_t = float(close_arr[row_idx])

                # 计算 T+1, T+2, T+3 收益率
                future_returns = {}
                for day_offset in range(1, FUTURE_DAYS + 1):
                    future_idx = row_idx + day_offset
                    if future_idx < n_rows:
                        future_close = float(close_arr[future_idx])
                        future_returns[f"ret_t{day_offset}"] = round(
                            (future_close - close_t) / close_t * 100, 4
                        )
                    else:
                        future_returns[f"ret_t{day_offset}"] = np.nan

                record = {
                    "symbol": symbol,
                    "name": names.get(symbol, ""),
                    "date": date_str,
                    "pattern": pattern_type.value,
                    "final_score": round(breakdown.final_score, 1),
                    "grade": breakdown.grade,
                    "specific_score": round(specific_score, 1),
                    "common_score": round(common_score, 1),
                    "macd_score": round(macd_score, 1),
                    "signal_score": round(signal_score, 1),
                    "risk_penalty": round(risk_penalty, 1),
                    "close_t": round(close_t, 2),
                }

                # 所有因子得分展平到 record
                for factor_name, factor_score in specific_items.items():
                    record[f"specific_{factor_name}"] = factor_score
                for factor_name, factor_score in common_items.items():
                    record[f"common_{factor_name}"] = factor_score
                for factor_name, factor_score in macd_items.items():
                    record[f"macd_{factor_name}"] = factor_score
                for factor_name, factor_score in signal_items.items():
                    record[f"signal_{factor_name}"] = factor_score
                for factor_name, factor_score in risk_items.items():
                    record[f"risk_{factor_name}"] = factor_score

                # 原始值（从 extra 提取）
                for key, val in result.extra.items():
                    if key != "specific_items" and not isinstance(val, dict):
                        record[f"raw_{key}"] = val

                record.update(future_returns)
                records.append(record)
                matched_count += 1

    elapsed = time.time() - start_time
    print(f"\n回测完成: 共命中 {matched_count} 次, 错误 {error_count}, 耗时 {elapsed:.1f}s")
    return pd.DataFrame(records)


def analyze_factor(
    df: pd.DataFrame,
    factor_col: str,
    label: str,
    pattern_filter: str | None = None,
) -> dict | None:
    """分析单个因子对收益率/胜率的影响。

    将因子按其不同得分值分组，统计每组的 T+1/T+2/T+3 均值收益率和胜率。
    """
    subset = df.copy()
    if pattern_filter:
        subset = subset[subset["pattern"] == pattern_filter]

    if factor_col not in subset.columns:
        return None

    valid = subset.dropna(subset=[factor_col])
    if len(valid) < 10:
        return None

    unique_values = sorted(valid[factor_col].unique())
    if len(unique_values) < 2:
        return None

    groups = []
    for val in unique_values:
        group_df = valid[valid[factor_col] == val]
        if len(group_df) < 3:
            continue

        group_info = {"factor_value": val, "count": len(group_df)}
        for day in range(1, FUTURE_DAYS + 1):
            ret_col = f"ret_t{day}"
            ret_data = group_df[ret_col].dropna()
            if len(ret_data) > 0:
                group_info[f"t{day}_mean"] = round(ret_data.mean(), 4)
                group_info[f"t{day}_median"] = round(ret_data.median(), 4)
                group_info[f"t{day}_win_rate"] = round((ret_data > 0).mean() * 100, 2)
            else:
                group_info[f"t{day}_mean"] = np.nan
                group_info[f"t{day}_median"] = np.nan
                group_info[f"t{day}_win_rate"] = np.nan
        groups.append(group_info)

    if len(groups) < 2:
        return None

    # 计算因子区分度：最高分组与最低分组的收益率差异
    sorted_groups = sorted(groups, key=lambda x: x["factor_value"])
    lowest = sorted_groups[0]
    highest = sorted_groups[-1]

    discrimination = {}
    for day in range(1, FUTURE_DAYS + 1):
        high_mean = highest.get(f"t{day}_mean", 0) or 0
        low_mean = lowest.get(f"t{day}_mean", 0) or 0
        high_wr = highest.get(f"t{day}_win_rate", 0) or 0
        low_wr = lowest.get(f"t{day}_win_rate", 0) or 0
        discrimination[f"t{day}_return_diff"] = round(high_mean - low_mean, 4)
        discrimination[f"t{day}_winrate_diff"] = round(high_wr - low_wr, 2)

    return {
        "factor_name": label,
        "factor_col": factor_col,
        "total_samples": len(valid),
        "groups": groups,
        "discrimination": discrimination,
    }


def generate_report(df: pd.DataFrame, all_analysis: dict) -> str:
    """生成完整的 Markdown 分析报告。"""
    lines = []
    lines.append("## 砖形图评分系统 — 单因子独立回测分析报告\n")
    lines.append(f"**回测区间**: {START_DATE} ~ {END_DATE}\n")
    lines.append(f"**总命中信号数**: {len(df)}\n")
    lines.append(f"**涉及股票数**: {df['symbol'].nunique()}\n")

    # 整体统计
    lines.append("### 一、整体收益统计\n")
    lines.append("| 指标 | T+1 | T+2 | T+3 |")
    lines.append("|------|-----|-----|-----|")
    for day in range(1, FUTURE_DAYS + 1):
        ret_col = f"ret_t{day}"
        valid = df[ret_col].dropna()
        if len(valid) > 0:
            pass  # 在表格下面统一输出
    # 构建表格行
    row_labels = ["样本数", "均值(%)", "中位数(%)", "胜率(>0%)"]
    for row_label in row_labels:
        row = f"| {row_label} |"
        for day in range(1, FUTURE_DAYS + 1):
            ret_col = f"ret_t{day}"
            valid = df[ret_col].dropna()
            if row_label == "样本数":
                row += f" {len(valid)} |"
            elif row_label == "均值(%)":
                row += f" {valid.mean():.2f} |"
            elif row_label == "中位数(%)":
                row += f" {valid.median():.2f} |"
            elif row_label == "胜率(>0%)":
                row += f" {(valid > 0).mean() * 100:.1f}% |"
        lines.append(row)

    # 按定式分别统计
    lines.append("\n### 二、各定式整体表现\n")
    lines.append("| 定式 | 命中数 | T+1均值(%) | T+1胜率 | T+2均值(%) | T+2胜率 | T+3均值(%) | T+3胜率 |")
    lines.append("|------|--------|-----------|---------|-----------|---------|-----------|---------|")
    for pattern in ["N型起跳", "横盘起跳", "上升波段延续"]:
        pdf = df[df["pattern"] == pattern]
        if len(pdf) == 0:
            continue
        row = f"| {pattern} | {len(pdf)} |"
        for day in range(1, FUTURE_DAYS + 1):
            ret_col = f"ret_t{day}"
            valid = pdf[ret_col].dropna()
            if len(valid) > 0:
                row += f" {valid.mean():.2f} | {(valid > 0).mean() * 100:.1f}% |"
            else:
                row += " - | - |"
        lines.append(row)

    # 逐定式逐因子分析
    section_num = 3
    for pattern_name, pattern_label in [
        ("N型起跳", "N型起跳"),
        ("横盘起跳", "横盘起跳"),
        ("上升波段延续", "上升波段延续"),
    ]:
        pattern_analysis = all_analysis.get(pattern_name, {})
        if not pattern_analysis:
            continue

        lines.append(f"\n### {_cn_num(section_num)}、{pattern_label} — 专属因子分析\n")
        section_num += 1

        # 因子区分度总览
        lines.append("#### 因子区分度总览\n")
        lines.append("| 因子 | 样本数 | T+1收益差(%) | T+1胜率差(pp) | T+2收益差(%) | T+2胜率差(pp) | T+3收益差(%) | T+3胜率差(pp) |")
        lines.append("|------|--------|-------------|--------------|-------------|--------------|-------------|--------------|")
        for factor_key, analysis in pattern_analysis.items():
            if analysis is None:
                continue
            disc = analysis["discrimination"]
            row = f"| {analysis['factor_name']} | {analysis['total_samples']} |"
            for day in range(1, FUTURE_DAYS + 1):
                ret_diff = disc.get(f"t{day}_return_diff", 0)
                wr_diff = disc.get(f"t{day}_winrate_diff", 0)
                ret_sign = "+" if ret_diff > 0 else ""
                wr_sign = "+" if wr_diff > 0 else ""
                row += f" {ret_sign}{ret_diff:.2f} | {wr_sign}{wr_diff:.1f} |"
            lines.append(row)

        # 每个因子的详细分组数据
        for factor_key, analysis in pattern_analysis.items():
            if analysis is None:
                continue
            lines.append(f"\n##### {analysis['factor_name']}（样本数: {analysis['total_samples']}）\n")
            lines.append("| 得分 | 信号数 | T+1均值(%) | T+1胜率 | T+2均值(%) | T+2胜率 | T+3均值(%) | T+3胜率 |")
            lines.append("|------|--------|-----------|---------|-----------|---------|-----------|---------|")
            for group in analysis["groups"]:
                val = group["factor_value"]
                cnt = group["count"]
                row = f"| {val} | {cnt} |"
                for day in range(1, FUTURE_DAYS + 1):
                    mean_val = group.get(f"t{day}_mean", np.nan)
                    wr_val = group.get(f"t{day}_win_rate", np.nan)
                    if not np.isnan(mean_val):
                        row += f" {mean_val:.2f} | {wr_val:.1f}% |"
                    else:
                        row += " - | - |"
                lines.append(row)

    # 通用因子分析（跨定式）
    common_analysis = all_analysis.get("通用因子", {})
    if common_analysis:
        lines.append(f"\n### {_cn_num(section_num)}、通用质量因子分析（跨定式）\n")
        section_num += 1

        lines.append("#### 因子区分度总览\n")
        lines.append("| 因子 | 样本数 | T+1收益差(%) | T+1胜率差(pp) | T+2收益差(%) | T+2胜率差(pp) | T+3收益差(%) | T+3胜率差(pp) |")
        lines.append("|------|--------|-------------|--------------|-------------|--------------|-------------|--------------|")
        for factor_key, analysis in common_analysis.items():
            if analysis is None:
                continue
            disc = analysis["discrimination"]
            row = f"| {analysis['factor_name']} | {analysis['total_samples']} |"
            for day in range(1, FUTURE_DAYS + 1):
                ret_diff = disc.get(f"t{day}_return_diff", 0)
                wr_diff = disc.get(f"t{day}_winrate_diff", 0)
                ret_sign = "+" if ret_diff > 0 else ""
                wr_sign = "+" if wr_diff > 0 else ""
                row += f" {ret_sign}{ret_diff:.2f} | {wr_sign}{wr_diff:.1f} |"
            lines.append(row)

        for factor_key, analysis in common_analysis.items():
            if analysis is None:
                continue
            lines.append(f"\n##### {analysis['factor_name']}（样本数: {analysis['total_samples']}）\n")
            lines.append("| 得分 | 信号数 | T+1均值(%) | T+1胜率 | T+2均值(%) | T+2胜率 | T+3均值(%) | T+3胜率 |")
            lines.append("|------|--------|-----------|---------|-----------|---------|-----------|---------|")
            for group in analysis["groups"]:
                val = group["factor_value"]
                cnt = group["count"]
                row = f"| {val} | {cnt} |"
                for day in range(1, FUTURE_DAYS + 1):
                    mean_val = group.get(f"t{day}_mean", np.nan)
                    wr_val = group.get(f"t{day}_win_rate", np.nan)
                    if not np.isnan(mean_val):
                        row += f" {mean_val:.2f} | {wr_val:.1f}% |"
                    else:
                        row += " - | - |"
                lines.append(row)

    # MACD 因子分析
    macd_analysis = all_analysis.get("MACD因子", {})
    if macd_analysis:
        lines.append(f"\n### {_cn_num(section_num)}、MACD环境因子分析\n")
        section_num += 1

        lines.append("#### 因子区分度总览\n")
        lines.append("| 因子 | 样本数 | T+1收益差(%) | T+1胜率差(pp) | T+2收益差(%) | T+2胜率差(pp) | T+3收益差(%) | T+3胜率差(pp) |")
        lines.append("|------|--------|-------------|--------------|-------------|--------------|-------------|--------------|")
        for factor_key, analysis in macd_analysis.items():
            if analysis is None:
                continue
            disc = analysis["discrimination"]
            row = f"| {analysis['factor_name']} | {analysis['total_samples']} |"
            for day in range(1, FUTURE_DAYS + 1):
                ret_diff = disc.get(f"t{day}_return_diff", 0)
                wr_diff = disc.get(f"t{day}_winrate_diff", 0)
                ret_sign = "+" if ret_diff > 0 else ""
                wr_sign = "+" if wr_diff > 0 else ""
                row += f" {ret_sign}{ret_diff:.2f} | {wr_sign}{wr_diff:.1f} |"
            lines.append(row)

        for factor_key, analysis in macd_analysis.items():
            if analysis is None:
                continue
            lines.append(f"\n##### {analysis['factor_name']}（样本数: {analysis['total_samples']}）\n")
            lines.append("| 得分 | 信号数 | T+1均值(%) | T+1胜率 | T+2均值(%) | T+2胜率 | T+3均值(%) | T+3胜率 |")
            lines.append("|------|--------|-----------|---------|-----------|---------|-----------|---------|")
            for group in analysis["groups"]:
                val = group["factor_value"]
                cnt = group["count"]
                row = f"| {val} | {cnt} |"
                for day in range(1, FUTURE_DAYS + 1):
                    mean_val = group.get(f"t{day}_mean", np.nan)
                    wr_val = group.get(f"t{day}_win_rate", np.nan)
                    if not np.isnan(mean_val):
                        row += f" {mean_val:.2f} | {wr_val:.1f}% |"
                    else:
                        row += " - | - |"
                lines.append(row)

    # 信号强度因子分析
    signal_analysis = all_analysis.get("信号强度", {})
    if signal_analysis:
        lines.append(f"\n### {_cn_num(section_num)}、信号强度因子分析\n")
        section_num += 1

        lines.append("#### 因子区分度总览\n")
        lines.append("| 因子 | 样本数 | T+1收益差(%) | T+1胜率差(pp) | T+2收益差(%) | T+2胜率差(pp) | T+3收益差(%) | T+3胜率差(pp) |")
        lines.append("|------|--------|-------------|--------------|-------------|--------------|-------------|--------------|")
        for factor_key, analysis in signal_analysis.items():
            if analysis is None:
                continue
            disc = analysis["discrimination"]
            row = f"| {analysis['factor_name']} | {analysis['total_samples']} |"
            for day in range(1, FUTURE_DAYS + 1):
                ret_diff = disc.get(f"t{day}_return_diff", 0)
                wr_diff = disc.get(f"t{day}_winrate_diff", 0)
                ret_sign = "+" if ret_diff > 0 else ""
                wr_sign = "+" if wr_diff > 0 else ""
                row += f" {ret_sign}{ret_diff:.2f} | {wr_sign}{wr_diff:.1f} |"
            lines.append(row)

        for factor_key, analysis in signal_analysis.items():
            if analysis is None:
                continue
            lines.append(f"\n##### {analysis['factor_name']}（样本数: {analysis['total_samples']}）\n")
            lines.append("| 得分 | 信号数 | T+1均值(%) | T+1胜率 | T+2均值(%) | T+2胜率 | T+3均值(%) | T+3胜率 |")
            lines.append("|------|--------|-----------|---------|-----------|---------|-----------|---------|")
            for group in analysis["groups"]:
                val = group["factor_value"]
                cnt = group["count"]
                row = f"| {val} | {cnt} |"
                for day in range(1, FUTURE_DAYS + 1):
                    mean_val = group.get(f"t{day}_mean", np.nan)
                    wr_val = group.get(f"t{day}_win_rate", np.nan)
                    if not np.isnan(mean_val):
                        row += f" {mean_val:.2f} | {wr_val:.1f}% |"
                    else:
                        row += " - | - |"
                lines.append(row)

    # 风险因子分析
    risk_analysis = all_analysis.get("风险因子", {})
    if risk_analysis:
        lines.append(f"\n### {_cn_num(section_num)}、风险因子影响分析\n")
        section_num += 1
        lines.append("> 风险因子采用「触发 vs 未触发」二分法分析，比较触发风险项时的收益率与未触发时的差异。\n")

        for factor_key, analysis in risk_analysis.items():
            if analysis is None:
                continue
            lines.append(f"\n##### {analysis['factor_name']}（样本数: {analysis['total_samples']}）\n")
            lines.append("| 状态 | 信号数 | T+1均值(%) | T+1胜率 | T+2均值(%) | T+2胜率 | T+3均值(%) | T+3胜率 |")
            lines.append("|------|--------|-----------|---------|-----------|---------|-----------|---------|")
            for group in analysis["groups"]:
                val = group["factor_value"]
                status_label = "未触发(0)" if val == 0 else f"触发({val})"
                cnt = group["count"]
                row = f"| {status_label} | {cnt} |"
                for day in range(1, FUTURE_DAYS + 1):
                    mean_val = group.get(f"t{day}_mean", np.nan)
                    wr_val = group.get(f"t{day}_win_rate", np.nan)
                    if not np.isnan(mean_val):
                        row += f" {mean_val:.2f} | {wr_val:.1f}% |"
                    else:
                        row += " - | - |"
                lines.append(row)

    # 结论
    lines.append(f"\n### {_cn_num(section_num)}、关键发现与结论\n")
    lines.append(_generate_conclusions(all_analysis))

    return "\n".join(lines)


def _generate_conclusions(all_analysis: dict) -> str:
    """根据分析结果自动生成结论。"""
    findings = []
    strong_factors = []
    weak_factors = []

    for category, factors in all_analysis.items():
        if not isinstance(factors, dict):
            continue
        for factor_key, analysis in factors.items():
            if analysis is None:
                continue
            disc = analysis.get("discrimination", {})
            t1_ret_diff = abs(disc.get("t1_return_diff", 0))
            t1_wr_diff = abs(disc.get("t1_winrate_diff", 0))
            t2_ret_diff = abs(disc.get("t2_return_diff", 0))
            t2_wr_diff = abs(disc.get("t2_winrate_diff", 0))

            avg_ret_diff = (t1_ret_diff + t2_ret_diff) / 2
            avg_wr_diff = (t1_wr_diff + t2_wr_diff) / 2

            factor_label = f"{category}/{analysis['factor_name']}"
            if avg_ret_diff >= 0.5 or avg_wr_diff >= 5:
                strong_factors.append((factor_label, avg_ret_diff, avg_wr_diff))
            elif avg_ret_diff < 0.15 and avg_wr_diff < 2:
                weak_factors.append((factor_label, avg_ret_diff, avg_wr_diff))

    strong_factors.sort(key=lambda x: x[1] + x[2] / 10, reverse=True)
    weak_factors.sort(key=lambda x: x[1] + x[2] / 10)

    text_lines = []
    if strong_factors:
        text_lines.append("#### 强区分度因子（高分组与低分组差异显著）\n")
        for label, ret_d, wr_d in strong_factors[:10]:
            text_lines.append(f"- **{label}**: 平均收益差 {ret_d:.2f}%, 平均胜率差 {wr_d:.1f}pp")
        text_lines.append("")

    if weak_factors:
        text_lines.append("#### 弱区分度因子（高分组与低分组差异不明显）\n")
        for label, ret_d, wr_d in weak_factors[:10]:
            text_lines.append(f"- **{label}**: 平均收益差 {ret_d:.2f}%, 平均胜率差 {wr_d:.1f}pp")
        text_lines.append("")

    text_lines.append("#### 建议\n")
    text_lines.append("- 强区分度因子应在评分体系中**保留或增加权重**，其分值变化对实际收益有显著影响。")
    text_lines.append("- 弱区分度因子可考虑**降低权重或移除**，其分值变化对实际收益影响甚微，增加了系统复杂度但不增加预测力。")
    text_lines.append("- 部分风险因子如果触发后收益率并未显著降低，可考虑减轻扣分力度。")

    return "\n".join(text_lines)


def _cn_num(n: int) -> str:
    """数字转中文序号"""
    mapping = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八", 9: "九", 10: "十"}
    return mapping.get(n, str(n))


def main():
    print("=" * 60)
    print("砖形图评分系统 — 单因子独立回测")
    print("=" * 60)

    # Step 1: 收集信号
    df = collect_signals()
    if df.empty:
        print("未命中任何信号，退出。")
        return

    # 保存原始数据
    csv_path = ROOT / "output" / "factor_backtest_raw.csv"
    csv_path.parent.mkdir(exist_ok=True)
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"\n原始数据已保存: {csv_path}")

    # Step 2: 逐因子分析
    print("\n开始因子分析...")
    all_analysis = {}

    # N型起跳专属因子
    n_specific = {}
    for col, label in [
        ("specific_超卖深度", "超卖深度(10分)"),
        ("specific_回调充分度", "回调充分度(10分)"),
        ("specific_价格与黄白线", "价格与黄白线(5分)"),
        ("specific_前段上涨基础", "前段上涨基础(5分)"),
    ]:
        n_specific[col] = analyze_factor(df, col, label, "N型起跳")
    all_analysis["N型起跳"] = n_specific

    # 横盘起跳专属因子
    sw_specific = {}
    for col, label in [
        ("specific_蓄势充分度", "蓄势充分度(12分)"),
        ("specific_突破弹性", "突破弹性(8分)"),
        ("specific_KDJ动能", "KDJ动能(5分)"),
        ("specific_价格强度", "价格强度(5分)"),
    ]:
        sw_specific[col] = analyze_factor(df, col, label, "横盘起跳")
    all_analysis["横盘起跳"] = sw_specific

    # 上升波段延续专属因子
    ut_specific = {}
    for col, label in [
        ("specific_趋势连续性", "趋势连续性(12分)"),
        ("specific_回调极短性", "回调极短性(8分)"),
        ("specific_砖值绝对水平", "砖值绝对水平(5分)"),
        ("specific_KDJ超买动能", "KDJ超买动能(5分)"),
    ]:
        ut_specific[col] = analyze_factor(df, col, label, "上升波段延续")
    all_analysis["上升波段延续"] = ut_specific

    # 通用质量因子（跨定式）
    common_factors = {}
    for col, label in [
        ("common_翻红力度比", "翻红力度比(7分)"),
        ("common_信号日涨幅", "信号日涨幅(6分)"),
        ("common_短趋vs多空", "短趋vs多空(6分)"),
        ("common_均线排列", "均线排列(4分)"),
        ("common_短趋斜率", "短趋斜率(3分)"),
        ("common_K线形态", "K线形态(4分)"),
    ]:
        common_factors[col] = analyze_factor(df, col, label, None)
    all_analysis["通用因子"] = common_factors

    # MACD 因子
    macd_factors = {}
    # N型/横盘共用
    for col, label in [
        ("macd_DIFF位置", "DIFF位置(10分)"),
        ("macd_MACD柱状态", "MACD柱状态(8分)"),
        ("macd_金叉确认", "金叉确认(7分)"),
    ]:
        combined = df[df["pattern"].isin(["N型起跳", "横盘起跳"])]
        if col in combined.columns and len(combined) > 0:
            macd_factors[col] = analyze_factor(combined, col, label + "[N型/横盘]", None)
    # 波段延续专用
    for col, label in [
        ("macd_DIFF趋势", "DIFF趋势(10分)"),
        ("macd_MACD柱趋势", "MACD柱趋势(8分)"),
        ("macd_DIFF水平", "DIFF水平(7分)"),
    ]:
        macd_factors[col] = analyze_factor(df, col, label + "[波段延续]", "上升波段延续")
    all_analysis["MACD因子"] = macd_factors

    # 信号强度因子
    signal_factors = {}
    for col, label in [
        ("signal_T日涨幅", "T日涨幅(10分)"),
        ("signal_涨幅质量", "涨幅质量(5分)"),
    ]:
        signal_factors[col] = analyze_factor(df, col, label, None)
    all_analysis["信号强度"] = signal_factors

    # 风险因子 — 二值分析 (触发 vs 未触发)
    risk_factors = {}
    risk_cols = [c for c in df.columns if c.startswith("risk_")]
    for col in risk_cols:
        label = col.replace("risk_", "")
        # 将风险值二值化: 0=未触发, 非0=触发
        df_copy = df.copy()
        df_copy[f"{col}_binary"] = (df_copy[col].fillna(0) != 0).astype(int)
        analysis = analyze_factor(df_copy, f"{col}_binary", label, None)
        if analysis:
            # 同时统计不同扣分值的分布
            risk_factors[col] = analyze_factor(df, col, label, None)
            if risk_factors[col] is None:
                risk_factors[col] = analysis
    all_analysis["风险因子"] = risk_factors

    # Step 3: 生成报告
    print("生成分析报告...")
    report = generate_report(df, all_analysis)
    report_path = ROOT / "output" / "砖形图评分因子回测分析报告.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"\n✅ 分析报告已保存: {report_path}")


if __name__ == "__main__":
    main()
