"""砖形图评分系统 + MACD 辅助回测脚本

扫描 2025-01-01 到 2026-03-31 所有交易日的所有股票，
找到砖形图评分系统触发的信号，记录 MACD 状态，
统计 T+0 ~ T+3 收益率，分析 MACD 如何辅助提高胜率和收益率。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app.chart_indicators import (
    compute_brick_indicator,
    compute_kdj_indicator,
    compute_macd_indicator,
    compute_zx_long_short,
    compute_zx_short_trend,
    moving_average,
)
from core.screening.brick_pattern_engine import (
    PatternType,
    _calc_indicators,
    check_prerequisites,
    compute_common_quality_score,
    compute_risk_penalty,
    detect_n_shape_jump,
    detect_sideways_jump,
    detect_uptrend_continue,
)
from core.data.time_index import build_date_index

START_DATE = "2025-01-01"
END_DATE = "2026-03-31"
FUTURE_DAYS = 3

ENABLED_PATTERNS = (PatternType.N_SHAPE_JUMP, PatternType.SIDEWAYS_JUMP, PatternType.UPTREND_CONTINUE)


def load_stock_list() -> pd.DataFrame:
    df = pd.read_csv(ROOT / "stocklist.csv", dtype={"symbol": str})
    df["symbol"] = df["symbol"].astype(str).str.zfill(6)
    return df


def load_daily_data(symbol: str) -> pd.DataFrame | None:
    fp = ROOT / "stock_daily_data" / f"{symbol}.csv"
    if not fp.exists():
        return None
    df = pd.read_csv(fp)
    if df.empty or "date" not in df.columns:
        return None
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    for col in ["open", "close", "high", "low", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "volume" not in df.columns:
        df["volume"] = 0.0
    return df


def calc_all_indicators(df: pd.DataFrame) -> dict[str, np.ndarray]:
    """计算砖形图指标 + MACD 指标"""
    indicators = _calc_indicators(df)
    close = indicators["close"]
    macd_result = compute_macd_indicator(close)
    indicators["macd_diff"] = macd_result["diff"]
    indicators["macd_dea"] = macd_result["dea"]
    indicators["macd_bar"] = macd_result["macd"]
    indicators["macd_cross_up"] = macd_result["cross_up"]
    indicators["macd_cross_down"] = macd_result["cross_down"]
    return indicators


def extract_macd_features(indicators: dict[str, np.ndarray], index: int) -> dict:
    """提取信号日的 MACD 特征"""
    diff = indicators["macd_diff"]
    dea = indicators["macd_dea"]
    bar = indicators["macd_bar"]
    cross_up = indicators["macd_cross_up"]

    diff_val = float(diff[index]) if np.isfinite(diff[index]) else 0
    dea_val = float(dea[index]) if np.isfinite(dea[index]) else 0
    bar_val = float(bar[index]) if np.isfinite(bar[index]) else 0

    # DIFF 在零轴上方/下方
    diff_above_zero = diff_val > 0

    # DIFF 在 DEA 上方（金叉状态）
    diff_above_dea = diff_val > dea_val

    # MACD 柱状图正/负
    bar_positive = bar_val > 0

    # MACD 柱状图趋势（连续增长天数）
    bar_increasing_days = 0
    for i in range(index, max(0, index - 10), -1):
        if i < 1:
            break
        if np.isfinite(bar[i]) and np.isfinite(bar[i - 1]) and bar[i] > bar[i - 1]:
            bar_increasing_days += 1
        else:
            break

    # 最近N天内是否出现金叉
    recent_golden_cross = 0
    for i in range(index, max(0, index - 10), -1):
        if cross_up[i]:
            recent_golden_cross = index - i
            break

    # DIFF 斜率（5日）
    if index >= 5:
        diff_slope = diff_val - float(diff[index - 5]) if np.isfinite(diff[index - 5]) else 0
    else:
        diff_slope = 0

    # DIFF 与 DEA 的差值（距离）
    diff_dea_gap = diff_val - dea_val

    # 前一天 MACD 柱状图
    prev_bar = float(bar[index - 1]) if index >= 1 and np.isfinite(bar[index - 1]) else 0

    # MACD 柱翻红（前日负或零，今日正）
    bar_turn_positive = prev_bar <= 0 and bar_val > 0

    # MACD 零轴附近金叉（DIFF 接近零轴 ±2%价格范围内金叉）
    close_val = float(indicators["close"][index])
    diff_near_zero = abs(diff_val) < close_val * 0.02 if close_val > 0 else False

    return {
        "diff": round(diff_val, 4),
        "dea": round(dea_val, 4),
        "bar": round(bar_val, 4),
        "diff_above_zero": diff_above_zero,
        "diff_above_dea": diff_above_dea,
        "bar_positive": bar_positive,
        "bar_increasing_days": bar_increasing_days,
        "recent_golden_cross": recent_golden_cross,
        "diff_slope_5d": round(diff_slope, 4),
        "diff_dea_gap": round(diff_dea_gap, 4),
        "bar_turn_positive": bar_turn_positive,
        "diff_near_zero": diff_near_zero,
        "prev_bar": round(prev_bar, 4),
    }


def calc_future_returns(close: np.ndarray, index: int, days: int = 3) -> dict:
    """计算 T+0 到 T+days 的收益率"""
    result = {}
    base_price = close[index]
    if base_price <= 0 or not np.isfinite(base_price):
        return {f"ret_t{d}": None for d in range(1, days + 1)}

    for d in range(1, days + 1):
        future_idx = index + d
        if future_idx < len(close) and np.isfinite(close[future_idx]):
            result[f"ret_t{d}"] = round((close[future_idx] - base_price) / base_price * 100, 4)
        else:
            result[f"ret_t{d}"] = None

    # 总收益 = T+3 相对 T 的收益
    if f"ret_t{days}" in result and result[f"ret_t{days}"] is not None:
        result["ret_total"] = result[f"ret_t{days}"]
    else:
        result["ret_total"] = None

    return result


def screen_single_day(
    indicators: dict[str, np.ndarray],
    index: int,
    symbol: str,
    name: str,
    date_str: str,
) -> dict | None:
    """对单只股票单个交易日执行完整检测，返回信号详情或 None"""
    prereq_ok, prereq_detail = check_prerequisites(indicators, index)
    if not prereq_ok:
        return None

    detectors = {
        PatternType.N_SHAPE_JUMP: detect_n_shape_jump,
        PatternType.SIDEWAYS_JUMP: detect_sideways_jump,
        PatternType.UPTREND_CONTINUE: detect_uptrend_continue,
    }

    best = None
    best_final = -1

    for pt in ENABLED_PATTERNS:
        detail = detectors[pt](indicators, index)
        if not detail.matched:
            continue

        common_score, common_items = compute_common_quality_score(indicators, index, pt)
        risk_penalty, risk_items, _ = compute_risk_penalty(indicators, index, pt)

        specific = detail.score
        final = max(0, specific + common_score + risk_penalty)

        if final > best_final:
            best_final = final
            best = {
                "symbol": symbol,
                "name": name,
                "date": date_str,
                "pattern": pt.value,
                "specific_score": specific,
                "common_score": common_score,
                "risk_penalty": risk_penalty,
                "final_score": final,
                "grade": _grade(final),
                "close": round(float(indicators["close"][index]), 2),
            }

    if best is None:
        return None

    # MACD 特征
    macd_features = extract_macd_features(indicators, index)
    best.update(macd_features)

    # 未来收益
    returns = calc_future_returns(indicators["close"], index, FUTURE_DAYS)
    best.update(returns)

    return best


def _grade(score: float) -> str:
    if score >= 85:
        return "S"
    if score >= 70:
        return "A"
    if score >= 55:
        return "B"
    if score >= 40:
        return "C"
    return "D"


def run_backtest():
    print("=" * 70)
    print("砖形图评分系统 + MACD 辅助回测")
    print(f"回测区间: {START_DATE} ~ {END_DATE}")
    print("=" * 70)

    stock_list = load_stock_list()
    symbols = stock_list["symbol"].tolist()
    names = dict(zip(stock_list["symbol"], stock_list["name"]))
    total_stocks = len(symbols)
    print(f"股票池: {total_stocks} 只")

    # 生成回测日期范围
    all_dates = pd.bdate_range(START_DATE, END_DATE).strftime("%Y-%m-%d").tolist()
    print(f"交易日范围: {len(all_dates)} 天")

    signals = []
    t0 = time.time()
    processed = 0
    skipped = 0

    for si, symbol in enumerate(symbols):
        df = load_daily_data(symbol)
        if df is None or len(df) < 120:
            skipped += 1
            continue

        # 预计算指标
        try:
            indicators = calc_all_indicators(df)
        except Exception:
            skipped += 1
            continue

        # 预构建日期索引
        date_index = build_date_index(df)
        stock_name = names.get(symbol, "")

        for date_str in all_dates:
            idx = date_index.get(date_str)
            if idx is None:
                continue
            if idx < 120:
                continue

            result = screen_single_day(indicators, idx, symbol, stock_name, date_str)
            if result is not None:
                signals.append(result)

        processed += 1
        if (si + 1) % 200 == 0:
            elapsed = time.time() - t0
            print(f"  已处理 {si + 1}/{total_stocks} ({elapsed:.0f}s), 信号数: {len(signals)}")

    elapsed = time.time() - t0
    print(f"\n处理完成: {processed} 只股票, 跳过 {skipped} 只, 耗时 {elapsed:.1f}s")
    print(f"共发现 {len(signals)} 个信号")

    if not signals:
        print("无信号，退出。")
        return

    df_signals = pd.DataFrame(signals)

    # 保存原始数据
    output_path = ROOT / "output" / "brick_macd_backtest.csv"
    output_path.parent.mkdir(exist_ok=True)
    df_signals.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(f"原始数据已保存: {output_path}")

    # ── 分析 ──
    analyze_results(df_signals)


def analyze_results(df: pd.DataFrame):
    """全面分析 MACD 对砖形图信号的辅助效果"""
    print("\n" + "=" * 70)
    print("回测结果分析")
    print("=" * 70)

    # 过滤掉没有未来收益的数据
    df_valid = df.dropna(subset=["ret_total"]).copy()
    df_valid["win"] = df_valid["ret_total"] > 0
    print(f"\n有效信号（有 T+3 数据）: {len(df_valid)} 个")

    # ── 1. 基准统计 ──
    print("\n" + "─" * 50)
    print("1. 基准统计（全部信号）")
    print("─" * 50)
    _print_return_stats(df_valid, "全部信号")

    # 按评分等级
    print("\n按评分等级:")
    for grade in ["S", "A", "B", "C", "D"]:
        sub = df_valid[df_valid["grade"] == grade]
        if len(sub) > 0:
            _print_return_stats(sub, f"  {grade}级")

    # 按定式类型
    print("\n按定式类型:")
    for pt in ["N型起跳", "横盘起跳", "上升波段延续"]:
        sub = df_valid[df_valid["pattern"] == pt]
        if len(sub) > 0:
            _print_return_stats(sub, f"  {pt}")

    # ── 2. 单一 MACD 条件分析 ──
    print("\n" + "─" * 50)
    print("2. 单一 MACD 条件分析")
    print("─" * 50)

    conditions = {
        "DIFF>0 (零轴上方)": df_valid["diff_above_zero"] == True,
        "DIFF<0 (零轴下方)": df_valid["diff_above_zero"] == False,
        "DIFF>DEA (金叉状态)": df_valid["diff_above_dea"] == True,
        "DIFF<DEA (死叉状态)": df_valid["diff_above_dea"] == False,
        "MACD柱>0": df_valid["bar_positive"] == True,
        "MACD柱<0": df_valid["bar_positive"] == False,
        "MACD柱翻红": df_valid["bar_turn_positive"] == True,
        "MACD柱连续增长≥2天": df_valid["bar_increasing_days"] >= 2,
        "MACD柱连续增长≥3天": df_valid["bar_increasing_days"] >= 3,
        "5日内金叉": (df_valid["recent_golden_cross"] > 0) & (df_valid["recent_golden_cross"] <= 5),
        "3日内金叉": (df_valid["recent_golden_cross"] > 0) & (df_valid["recent_golden_cross"] <= 3),
        "DIFF在零轴附近": df_valid["diff_near_zero"] == True,
        "DIFF 5日斜率>0": df_valid["diff_slope_5d"] > 0,
        "DIFF 5日斜率<0": df_valid["diff_slope_5d"] < 0,
    }

    results = []
    for label, mask in conditions.items():
        sub = df_valid[mask]
        if len(sub) < 5:
            continue
        stats = _calc_stats(sub)
        stats["条件"] = label
        stats["信号数"] = len(sub)
        results.append(stats)
        _print_return_stats(sub, f"  {label}")

    # ── 3. 组合条件分析 ──
    print("\n" + "─" * 50)
    print("3. MACD 组合条件分析（探索最优组合）")
    print("─" * 50)

    combos = {
        "DIFF>0 + DIFF>DEA": (df_valid["diff_above_zero"]) & (df_valid["diff_above_dea"]),
        "DIFF>0 + MACD柱>0": (df_valid["diff_above_zero"]) & (df_valid["bar_positive"]),
        "DIFF>0 + MACD柱翻红": (df_valid["diff_above_zero"]) & (df_valid["bar_turn_positive"]),
        "DIFF>0 + 柱连增≥2天": (df_valid["diff_above_zero"]) & (df_valid["bar_increasing_days"] >= 2),
        "DIFF>DEA + 柱连增≥2天": (df_valid["diff_above_dea"]) & (df_valid["bar_increasing_days"] >= 2),
        "DIFF>0 + 5日内金叉": (df_valid["diff_above_zero"]) & (df_valid["recent_golden_cross"] > 0) & (df_valid["recent_golden_cross"] <= 5),
        "零轴附近 + DIFF>DEA": (df_valid["diff_near_zero"]) & (df_valid["diff_above_dea"]),
        "DIFF<0 + MACD柱翻红": (~df_valid["diff_above_zero"]) & (df_valid["bar_turn_positive"]),
        "DIFF>0 + 斜率>0 + 柱>0": (df_valid["diff_above_zero"]) & (df_valid["diff_slope_5d"] > 0) & (df_valid["bar_positive"]),
        "DIFF>DEA + 斜率>0": (df_valid["diff_above_dea"]) & (df_valid["diff_slope_5d"] > 0),
        "金叉状态 + 柱连增≥2 + DIFF>0": (df_valid["diff_above_dea"]) & (df_valid["bar_increasing_days"] >= 2) & (df_valid["diff_above_zero"]),
    }

    for label, mask in combos.items():
        sub = df_valid[mask]
        if len(sub) < 5:
            print(f"  {label}: 信号数不足 ({len(sub)})")
            continue
        _print_return_stats(sub, f"  {label}")

    # ── 4. 按评分等级 × MACD 条件交叉分析 ──
    print("\n" + "─" * 50)
    print("4. 评分等级 × MACD 条件交叉分析")
    print("─" * 50)

    key_conditions = {
        "DIFF>0": df_valid["diff_above_zero"],
        "DIFF>DEA": df_valid["diff_above_dea"],
        "MACD柱>0": df_valid["bar_positive"],
    }

    for grade in ["S", "A", "B"]:
        grade_mask = df_valid["grade"] == grade
        grade_sub = df_valid[grade_mask]
        if len(grade_sub) < 5:
            continue
        print(f"\n  [{grade}级] 基准:")
        _print_return_stats(grade_sub, f"    {grade}级全部")

        for cond_label, cond_mask in key_conditions.items():
            sub = df_valid[grade_mask & cond_mask]
            if len(sub) >= 3:
                _print_return_stats(sub, f"    {grade}级+{cond_label}")
            anti_sub = df_valid[grade_mask & ~cond_mask]
            if len(anti_sub) >= 3:
                _print_return_stats(anti_sub, f"    {grade}级+非{cond_label}")

    # ── 5. 按定式 × MACD 交叉分析 ──
    print("\n" + "─" * 50)
    print("5. 定式类型 × MACD 条件交叉分析")
    print("─" * 50)

    for pt in ["N型起跳", "横盘起跳", "上升波段延续"]:
        pt_mask = df_valid["pattern"] == pt
        pt_sub = df_valid[pt_mask]
        if len(pt_sub) < 5:
            continue
        print(f"\n  [{pt}] 基准:")
        _print_return_stats(pt_sub, f"    全部")

        for cond_label, cond_mask in key_conditions.items():
            sub = df_valid[pt_mask & cond_mask]
            if len(sub) >= 3:
                _print_return_stats(sub, f"    +{cond_label}")

        # 特别分析: MACD柱翻红
        sub = df_valid[pt_mask & df_valid["bar_turn_positive"]]
        if len(sub) >= 3:
            _print_return_stats(sub, f"    +MACD柱翻红")

        # DIFF斜率>0
        sub = df_valid[pt_mask & (df_valid["diff_slope_5d"] > 0)]
        if len(sub) >= 3:
            _print_return_stats(sub, f"    +DIFF斜率>0")

    # ── 6. MACD 分段量化分析 ──
    print("\n" + "─" * 50)
    print("6. MACD 数值分段分析")
    print("─" * 50)

    # DIFF-DEA 差值分段
    print("\n  DIFF-DEA 差值分段:")
    bins = [(-np.inf, -0.5), (-0.5, 0), (0, 0.2), (0.2, 0.5), (0.5, np.inf)]
    for lo, hi in bins:
        mask = (df_valid["diff_dea_gap"] > lo) & (df_valid["diff_dea_gap"] <= hi)
        sub = df_valid[mask]
        if len(sub) >= 5:
            label = f"    DIFF-DEA ∈ ({lo:.1f}, {hi:.1f}]" if np.isfinite(lo) else f"    DIFF-DEA ≤ {hi:.1f}"
            _print_return_stats(sub, label)

    # MACD 柱连续增长天数
    print("\n  MACD 柱连续增长天数:")
    for d in range(0, 6):
        mask = df_valid["bar_increasing_days"] == d
        sub = df_valid[mask]
        if len(sub) >= 5:
            _print_return_stats(sub, f"    连增{d}天")

    # ── 7. 最终推荐策略汇总 ──
    print("\n" + "─" * 50)
    print("7. 策略对比汇总表")
    print("─" * 50)

    all_strategies = {
        "基准(无MACD过滤)": pd.Series([True] * len(df_valid)),
        "DIFF>0": df_valid["diff_above_zero"],
        "DIFF>DEA": df_valid["diff_above_dea"],
        "MACD柱>0": df_valid["bar_positive"],
        "DIFF>0+DIFF>DEA": (df_valid["diff_above_zero"]) & (df_valid["diff_above_dea"]),
        "DIFF>0+柱>0": (df_valid["diff_above_zero"]) & (df_valid["bar_positive"]),
        "DIFF>0+柱翻红": (df_valid["diff_above_zero"]) & (df_valid["bar_turn_positive"]),
        "DIFF>DEA+柱连增≥2": (df_valid["diff_above_dea"]) & (df_valid["bar_increasing_days"] >= 2),
        "DIFF>0+斜率>0+柱>0": (df_valid["diff_above_zero"]) & (df_valid["diff_slope_5d"] > 0) & (df_valid["bar_positive"]),
    }

    summary_rows = []
    for name, mask in all_strategies.items():
        sub = df_valid[mask]
        if len(sub) < 3:
            continue
        stats = _calc_stats(sub)
        stats["策略"] = name
        stats["信号数"] = len(sub)
        summary_rows.append(stats)

    if summary_rows:
        df_summary = pd.DataFrame(summary_rows)
        cols = ["策略", "信号数", "胜率%", "T+1均值%", "T+2均值%", "T+3均值%", "T+1中位%", "T+2中位%", "T+3中位%", "最大亏%"]
        for c in cols:
            if c not in df_summary.columns:
                df_summary[c] = ""
        print(f"\n{'策略':<25} {'信号':>5} {'胜率':>7} {'T1均':>7} {'T2均':>7} {'T3均':>7} {'T1中':>7} {'T2中':>7} {'T3中':>7} {'最大亏':>7}")
        print("-" * 95)
        for _, row in df_summary.iterrows():
            print(f"{row['策略']:<25} {row['信号数']:>5} {row['胜率%']:>6.1f}% {row['T+1均值%']:>6.2f}% {row['T+2均值%']:>6.2f}% {row['T+3均值%']:>6.2f}% {row['T+1中位%']:>6.2f}% {row['T+2中位%']:>6.2f}% {row['T+3中位%']:>6.2f}% {row['最大亏%']:>6.1f}%")

        summary_path = ROOT / "output" / "brick_macd_summary.csv"
        df_summary.to_csv(summary_path, index=False, encoding="utf-8-sig")
        print(f"\n汇总表已保存: {summary_path}")


def _calc_stats(df: pd.DataFrame) -> dict:
    """计算收益统计"""
    n = len(df)
    win_rate = (df["ret_total"] > 0).mean() * 100

    stats = {
        "胜率%": round(win_rate, 1),
    }

    for d in range(1, FUTURE_DAYS + 1):
        col = f"ret_t{d}"
        valid = df[col].dropna()
        if len(valid) > 0:
            stats[f"T+{d}均值%"] = round(valid.mean(), 2)
            stats[f"T+{d}中位%"] = round(valid.median(), 2)
        else:
            stats[f"T+{d}均值%"] = 0
            stats[f"T+{d}中位%"] = 0

    valid_total = df["ret_total"].dropna()
    if len(valid_total) > 0:
        stats["最大亏%"] = round(valid_total.min(), 1)
        stats["最大盈%"] = round(valid_total.max(), 1)
    else:
        stats["最大亏%"] = 0
        stats["最大盈%"] = 0

    return stats


def _print_return_stats(df: pd.DataFrame, label: str):
    """打印收益统计"""
    n = len(df)
    if n == 0:
        print(f"{label}: 无数据")
        return

    win_rate = (df["ret_total"] > 0).mean() * 100

    t1 = df["ret_t1"].dropna()
    t2 = df["ret_t2"].dropna()
    t3 = df["ret_t3"].dropna()

    t1_mean = t1.mean() if len(t1) > 0 else 0
    t2_mean = t2.mean() if len(t2) > 0 else 0
    t3_mean = t3.mean() if len(t3) > 0 else 0
    t1_med = t1.median() if len(t1) > 0 else 0
    t2_med = t2.median() if len(t2) > 0 else 0
    t3_med = t3.median() if len(t3) > 0 else 0

    print(f"{label}: N={n:>5}, 胜率={win_rate:>5.1f}%, "
          f"T1={t1_mean:>+5.2f}%(med {t1_med:>+5.2f}%), "
          f"T2={t2_mean:>+5.2f}%(med {t2_med:>+5.2f}%), "
          f"T3={t3_mean:>+5.2f}%(med {t3_med:>+5.2f}%)")


if __name__ == "__main__":
    run_backtest()
