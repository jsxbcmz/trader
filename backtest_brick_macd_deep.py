"""深度分析：聚焦 MACD 反直觉发现

第一轮回测发现 DIFF<0 反而表现更好，这里深入探究原因和最优策略。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent

def main():
    df = pd.read_csv(ROOT / "output" / "brick_macd_backtest.csv")
    df = df.dropna(subset=["ret_total"]).copy()
    df["win"] = df["ret_total"] > 0

    print("=" * 80)
    print("砖形图 + MACD 深度分析")
    print(f"有效信号: {len(df)} 个")
    print("=" * 80)

    # ── A. DIFF<0 为什么表现好？ ──
    print("\n" + "━" * 80)
    print("A. DIFF<0 为什么表现好？分价格区间对比")
    print("━" * 80)

    # 看下 DIFF<0 和 DIFF>0 的股价分布差异
    diff_neg = df[~df["diff_above_zero"]]
    diff_pos = df[df["diff_above_zero"]]
    print(f"\n  DIFF<0 平均股价: {diff_neg['close'].mean():.2f}, 中位: {diff_neg['close'].median():.2f}")
    print(f"  DIFF>0 平均股价: {diff_pos['close'].mean():.2f}, 中位: {diff_pos['close'].median():.2f}")
    print(f"  DIFF<0 定式分布: {diff_neg['pattern'].value_counts().to_dict()}")
    print(f"  DIFF>0 定式分布: {diff_pos['pattern'].value_counts().to_dict()}")
    print(f"  DIFF<0 等级分布: {diff_neg['grade'].value_counts().sort_index().to_dict()}")
    print(f"  DIFF>0 等级分布: {diff_pos['grade'].value_counts().sort_index().to_dict()}")

    # ── B. N型起跳专项分析 ──
    print("\n" + "━" * 80)
    print("B. N型起跳 × MACD 条件深度分析（最大子集，43k信号）")
    print("━" * 80)

    n_shape = df[df["pattern"] == "N型起跳"]

    conditions_n = {
        "基准(全部)":                    pd.Series([True] * len(n_shape), index=n_shape.index),
        "DIFF<0":                       ~n_shape["diff_above_zero"],
        "DIFF>0":                       n_shape["diff_above_zero"],
        "DIFF<0 + MACD柱翻红":          (~n_shape["diff_above_zero"]) & (n_shape["bar_turn_positive"]),
        "DIFF>0 + MACD柱翻红":          (n_shape["diff_above_zero"]) & (n_shape["bar_turn_positive"]),
        "DIFF<0 + 柱<0":               (~n_shape["diff_above_zero"]) & (~n_shape["bar_positive"]),
        "DIFF<0 + DIFF<DEA":            (~n_shape["diff_above_zero"]) & (~n_shape["diff_above_dea"]),
        "DIFF<0 + DIFF>DEA(零下金叉)":   (~n_shape["diff_above_zero"]) & (n_shape["diff_above_dea"]),
        "DIFF>0 + DIFF<DEA(零上回调)":   (n_shape["diff_above_zero"]) & (~n_shape["diff_above_dea"]),
        "DIFF在零轴附近":               n_shape["diff_near_zero"],
        "零轴附近 + 柱翻红":            (n_shape["diff_near_zero"]) & (n_shape["bar_turn_positive"]),
        "零轴附近 + DIFF>DEA":          (n_shape["diff_near_zero"]) & (n_shape["diff_above_dea"]),
    }

    _print_strategy_table("N型起跳", n_shape, conditions_n)

    # ── C. 按评分区间 × MACD分析 ──
    print("\n" + "━" * 80)
    print("C. 评分区间 × DIFF位置 交叉分析")
    print("━" * 80)

    score_bins = [(0, 50), (50, 60), (60, 70), (70, 80), (80, 90), (90, 100)]
    for lo, hi in score_bins:
        mask = (df["final_score"] >= lo) & (df["final_score"] < hi)
        sub = df[mask]
        if len(sub) < 20:
            continue
        diff_neg_sub = sub[~sub["diff_above_zero"]]
        diff_pos_sub = sub[sub["diff_above_zero"]]
        print(f"\n  分数 [{lo},{hi}):")
        _print_one_line(sub, "    全部")
        if len(diff_neg_sub) >= 5:
            _print_one_line(diff_neg_sub, "    DIFF<0")
        if len(diff_pos_sub) >= 5:
            _print_one_line(diff_pos_sub, "    DIFF>0")

    # ── D. MACD 柱翻红专项分析 ──
    print("\n" + "━" * 80)
    print("D. MACD 柱翻红（bar_turn_positive）专项分析")
    print("━" * 80)

    bar_turn = df[df["bar_turn_positive"]]
    bar_not_turn = df[~df["bar_turn_positive"]]

    print(f"\n  柱翻红信号: {len(bar_turn)} 个, 非柱翻红: {len(bar_not_turn)} 个")
    _print_one_line(bar_turn, "  柱翻红")
    _print_one_line(bar_not_turn, "  非柱翻红")

    # 柱翻红 × 定式
    for pt in ["N型起跳", "横盘起跳", "上升波段延续"]:
        sub = bar_turn[bar_turn["pattern"] == pt]
        if len(sub) >= 5:
            _print_one_line(sub, f"  柱翻红+{pt}")

    # 柱翻红 × 评分等级
    for grade in ["S", "A", "B", "C"]:
        sub = bar_turn[bar_turn["grade"] == grade]
        if len(sub) >= 5:
            _print_one_line(sub, f"  柱翻红+{grade}级")

    # ── E. 最优策略搜索 ──
    print("\n" + "━" * 80)
    print("E. 最优 MACD 辅助策略搜索（信号数≥50，按T+3收益排序）")
    print("━" * 80)

    all_strategies = {}

    # 单条件
    single = {
        "DIFF<0": ~df["diff_above_zero"],
        "DIFF>0": df["diff_above_zero"],
        "DIFF>DEA": df["diff_above_dea"],
        "DIFF<DEA": ~df["diff_above_dea"],
        "柱>0": df["bar_positive"],
        "柱<0": ~df["bar_positive"],
        "柱翻红": df["bar_turn_positive"],
        "斜率>0": df["diff_slope_5d"] > 0,
        "斜率<0": df["diff_slope_5d"] < 0,
        "零轴附近": df["diff_near_zero"],
        "柱连增1天": df["bar_increasing_days"] == 1,
        "柱连增≥2天": df["bar_increasing_days"] >= 2,
    }

    # 双条件
    pairs = {}
    keys = list(single.keys())
    for i, k1 in enumerate(keys):
        for k2 in keys[i+1:]:
            m = single[k1] & single[k2]
            cnt = m.sum()
            if cnt >= 50:
                pairs[f"{k1}+{k2}"] = m

    all_strategies.update(single)
    all_strategies.update(pairs)

    # 加入定式 × MACD
    for pt_name, pt_label in [("N型起跳", "N型"), ("横盘起跳", "横盘"), ("上升波段延续", "波段")]:
        pt_mask = df["pattern"] == pt_name
        for cond_name, cond_mask in single.items():
            m = pt_mask & cond_mask
            cnt = m.sum()
            if cnt >= 50:
                all_strategies[f"{pt_label}+{cond_name}"] = m

    # 加入等级 × MACD
    for grade in ["S", "A", "B", "C"]:
        g_mask = df["grade"] == grade
        for cond_name, cond_mask in single.items():
            m = g_mask & cond_mask
            cnt = m.sum()
            if cnt >= 50:
                all_strategies[f"{grade}级+{cond_name}"] = m

    rows = []
    for name, mask in all_strategies.items():
        sub = df[mask]
        n = len(sub)
        if n < 50:
            continue
        win_rate = (sub["ret_total"] > 0).mean() * 100
        t1 = sub["ret_t1"].mean()
        t2 = sub["ret_t2"].mean()
        t3 = sub["ret_t3"].mean()
        t1_med = sub["ret_t1"].median()
        t2_med = sub["ret_t2"].median()
        t3_med = sub["ret_t3"].median()
        rows.append({
            "策略": name, "N": n,
            "胜率%": round(win_rate, 1),
            "T1均%": round(t1, 2), "T2均%": round(t2, 2), "T3均%": round(t3, 2),
            "T1中%": round(t1_med, 2), "T2中%": round(t2_med, 2), "T3中%": round(t3_med, 2),
        })

    df_all = pd.DataFrame(rows)

    # 按 T+3 中位数收益排序（中位数更稳健）
    df_all = df_all.sort_values("T3中%", ascending=False)

    print(f"\n  共 {len(df_all)} 个策略, 按 T+3 中位数收益排序 TOP 30:")
    print(f"\n  {'策略':<30} {'N':>6} {'胜率':>6} {'T1均':>6} {'T2均':>6} {'T3均':>6} {'T1中':>6} {'T2中':>6} {'T3中':>6}")
    print("  " + "-" * 90)
    for _, row in df_all.head(30).iterrows():
        print(f"  {row['策略']:<30} {row['N']:>6} {row['胜率%']:>5.1f}% {row['T1均%']:>5.2f}% {row['T2均%']:>5.2f}% {row['T3均%']:>5.2f}% {row['T1中%']:>5.2f}% {row['T2中%']:>5.2f}% {row['T3中%']:>5.2f}%")

    # 按胜率排序
    df_win = df_all.sort_values("胜率%", ascending=False)
    print(f"\n  按胜率排序 TOP 30:")
    print(f"\n  {'策略':<30} {'N':>6} {'胜率':>6} {'T1均':>6} {'T2均':>6} {'T3均':>6} {'T1中':>6} {'T2中':>6} {'T3中':>6}")
    print("  " + "-" * 90)
    for _, row in df_win.head(30).iterrows():
        print(f"  {row['策略']:<30} {row['N']:>6} {row['胜率%']:>5.1f}% {row['T1均%']:>5.2f}% {row['T2均%']:>5.2f}% {row['T3均%']:>5.2f}% {row['T1中%']:>5.2f}% {row['T2中%']:>5.2f}% {row['T3中%']:>5.2f}%")

    # 按 T+3 均值收益排序
    df_t3 = df_all.sort_values("T3均%", ascending=False)
    print(f"\n  按 T+3 均值收益排序 TOP 30:")
    print(f"\n  {'策略':<30} {'N':>6} {'胜率':>6} {'T1均':>6} {'T2均':>6} {'T3均':>6} {'T1中':>6} {'T2中':>6} {'T3中':>6}")
    print("  " + "-" * 90)
    for _, row in df_t3.head(30).iterrows():
        print(f"  {row['策略']:<30} {row['N']:>6} {row['胜率%']:>5.1f}% {row['T1均%']:>5.2f}% {row['T2均%']:>5.2f}% {row['T3均%']:>5.2f}% {row['T1中%']:>5.2f}% {row['T2中%']:>5.2f}% {row['T3中%']:>5.2f}%")

    # ── F. 推荐策略总结 ──
    print("\n" + "━" * 80)
    print("F. 推荐 MACD 辅助评分方案")
    print("━" * 80)

    # 对比基准
    base_win = (df["ret_total"] > 0).mean() * 100
    base_t3 = df["ret_t3"].mean()
    base_t3_med = df["ret_t3"].median()

    print(f"\n  基准: N={len(df)}, 胜率={base_win:.1f}%, T+3均值={base_t3:.2f}%, T+3中位={base_t3_med:.2f}%")

    # 挑选几个有代表性的策略
    reco = {
        "方案1: DIFF<0过滤":       ~df["diff_above_zero"],
        "方案2: DIFF<DEA过滤":     ~df["diff_above_dea"],
        "方案3: 柱翻红过滤":       df["bar_turn_positive"],
        "方案4: 零轴附近":         df["diff_near_zero"],
        "方案5: DIFF<0+柱翻红":    (~df["diff_above_zero"]) & (df["bar_turn_positive"]),
        "方案6: N型+DIFF<0":       (df["pattern"] == "N型起跳") & (~df["diff_above_zero"]),
        "方案7: N型+柱翻红":       (df["pattern"] == "N型起跳") & (df["bar_turn_positive"]),
        "方案8: 横盘+柱翻红":      (df["pattern"] == "横盘起跳") & (df["bar_turn_positive"]),
    }

    print(f"\n  {'方案':<25} {'N':>6} {'胜率':>7} {'T1均':>7} {'T2均':>7} {'T3均':>7} {'T3中':>7} {'vs基准':>8}")
    print("  " + "-" * 80)
    for name, mask in reco.items():
        sub = df[mask]
        n = len(sub)
        if n < 5:
            continue
        wr = (sub["ret_total"] > 0).mean() * 100
        t1 = sub["ret_t1"].mean()
        t2 = sub["ret_t2"].mean()
        t3 = sub["ret_t3"].mean()
        t3_med = sub["ret_t3"].median()
        delta = t3 - base_t3
        print(f"  {name:<25} {n:>6} {wr:>6.1f}% {t1:>+6.2f}% {t2:>+6.2f}% {t3:>+6.2f}% {t3_med:>+6.2f}% {delta:>+7.2f}%")


def _print_strategy_table(title: str, df: pd.DataFrame, conditions: dict):
    print(f"\n  {'条件':<30} {'N':>6} {'胜率':>6} {'T1均':>6} {'T2均':>6} {'T3均':>6} {'T1中':>6} {'T2中':>6} {'T3中':>6}")
    print("  " + "-" * 90)
    for name, mask in conditions.items():
        sub = df[mask]
        n = len(sub)
        if n < 5:
            continue
        wr = (sub["ret_total"] > 0).mean() * 100
        t1 = sub["ret_t1"].mean()
        t2 = sub["ret_t2"].mean()
        t3 = sub["ret_t3"].mean()
        t1_med = sub["ret_t1"].median()
        t2_med = sub["ret_t2"].median()
        t3_med = sub["ret_t3"].median()
        print(f"  {name:<30} {n:>6} {wr:>5.1f}% {t1:>5.2f}% {t2:>5.2f}% {t3:>5.2f}% {t1_med:>5.2f}% {t2_med:>5.2f}% {t3_med:>5.2f}%")


def _print_one_line(df: pd.DataFrame, label: str):
    n = len(df)
    if n == 0:
        print(f"{label}: 无数据")
        return
    wr = (df["ret_total"] > 0).mean() * 100
    t1 = df["ret_t1"].mean()
    t2 = df["ret_t2"].mean()
    t3 = df["ret_t3"].mean()
    t3_med = df["ret_t3"].median()
    print(f"{label}: N={n:>5}, 胜率={wr:>5.1f}%, T1={t1:>+5.2f}%, T2={t2:>+5.2f}%, T3={t3:>+5.2f}%(med {t3_med:>+5.2f}%)")


if __name__ == "__main__":
    main()
