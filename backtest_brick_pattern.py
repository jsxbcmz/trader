"""砖形图定式评分系统回测脚本。

扫描 2025-01-01 ~ 2026-04-30 期间所有股票，
记录每次定式命中的 T/T+1/T+2 收益率，按评分等级统计有效性。
"""

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
    compute_common_quality_score,
    compute_macd_auxiliary_score,
    compute_risk_penalty,
    compute_signal_strength_score,
    detect_n_shape_jump,
    detect_sideways_jump,
    detect_uptrend_continue,
    ScoreBreakdown,
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
    t0 = time.time()

    for idx, symbol in enumerate(symbols):
        if (idx + 1) % 200 == 0 or idx == 0:
            elapsed = time.time() - t0
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
        date_strs = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d").values
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

            for pt in ENABLED_PATTERNS:
                detector = DETECTORS[pt]
                result = detector(indicators, row_idx)
                if not result.matched:
                    continue

                specific_score = result.score
                specific_items = result.extra.get("specific_items", {})

                common_score, common_items = compute_common_quality_score(
                    indicators, row_idx, pt,
                )
                macd_score, macd_items = compute_macd_auxiliary_score(
                    indicators, row_idx, pt,
                )
                risk_penalty, risk_items, _ = compute_risk_penalty(
                    indicators, row_idx, pt,
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

                final_score = breakdown.final_score
                grade = breakdown.grade

                close_t = float(close_arr[row_idx])

                ret_t0 = np.nan
                ret_t1 = np.nan
                ret_t2 = np.nan

                if row_idx >= 1:
                    close_prev = float(close_arr[row_idx - 1])
                    if close_prev > 0:
                        ret_t0 = (close_t - close_prev) / close_prev * 100

                if row_idx + 1 < n_rows:
                    close_t1 = float(close_arr[row_idx + 1])
                    ret_t1 = (close_t1 - close_t) / close_t * 100

                if row_idx + 2 < n_rows:
                    close_t2 = float(close_arr[row_idx + 2])
                    ret_t2 = (close_t2 - close_t) / close_t * 100

                records.append({
                    "symbol": symbol,
                    "name": names.get(symbol, ""),
                    "date": date_str,
                    "pattern": pt.value,
                    "final_score": round(final_score, 1),
                    "grade": grade,
                    "specific_score": round(specific_score, 1),
                    "common_score": round(common_score, 1),
                    "macd_score": round(macd_score, 1),
                    "signal_score": round(signal_score, 1),
                    "risk_penalty": round(risk_penalty, 1),
                    "close_t": round(close_t, 2),
                    "ret_t0": round(ret_t0, 4) if not np.isnan(ret_t0) else np.nan,
                    "ret_t1": round(ret_t1, 4) if not np.isnan(ret_t1) else np.nan,
                    "ret_t2": round(ret_t2, 4) if not np.isnan(ret_t2) else np.nan,
                })
                matched_count += 1

    elapsed = time.time() - t0
    print(f"\n回测完成: 共命中 {matched_count} 次, 错误 {error_count}, 耗时 {elapsed:.1f}s")

    if not records:
        print("未命中任何定式，无法生成报告。")
        return

    result_df = pd.DataFrame(records)
    csv_path = ROOT / "output" / "brick_pattern_backtest.csv"
    csv_path.parent.mkdir(exist_ok=True)
    result_df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"明细数据已保存: {csv_path}")

    generate_report(result_df)


def generate_report(df: pd.DataFrame):
    """生成回测分析报告。"""
    report_lines = []
    report_lines.append("# 砖形图定式评分系统回测报告")
    report_lines.append(f"\n**回测区间**: {START_DATE} ~ {END_DATE}")
    report_lines.append(f"**总命中次数**: {len(df)}")

    unique_stocks = df["symbol"].nunique()
    unique_dates = df["date"].nunique()
    report_lines.append(f"**涉及股票数**: {unique_stocks}")
    report_lines.append(f"**涉及交易日数**: {unique_dates}")

    # ── 1. 整体收益统计 ──
    report_lines.append("\n## 1. 整体收益统计\n")

    valid_t0 = df["ret_t0"].dropna()
    valid_t1 = df["ret_t1"].dropna()
    valid_t2 = df["ret_t2"].dropna()

    report_lines.append("| 指标 | T日收益率(%) | T+1 收益率(%) | T+2 收益率(%) |")
    report_lines.append("|------|-------------|--------------|--------------|")
    report_lines.append(f"| 样本数 | {len(valid_t0)} | {len(valid_t1)} | {len(valid_t2)} |")
    report_lines.append(f"| 均值 | {valid_t0.mean():.4f} | {valid_t1.mean():.4f} | {valid_t2.mean():.4f} |")
    report_lines.append(f"| 中位数 | {valid_t0.median():.4f} | {valid_t1.median():.4f} | {valid_t2.median():.4f} |")
    report_lines.append(f"| 标准差 | {valid_t0.std():.4f} | {valid_t1.std():.4f} | {valid_t2.std():.4f} |")
    report_lines.append(f"| 最大值 | {valid_t0.max():.4f} | {valid_t1.max():.4f} | {valid_t2.max():.4f} |")
    report_lines.append(f"| 最小值 | {valid_t0.min():.4f} | {valid_t1.min():.4f} | {valid_t2.min():.4f} |")
    report_lines.append(f"| 胜率(>0) | {(valid_t0 > 0).mean():.2%} | {(valid_t1 > 0).mean():.2%} | {(valid_t2 > 0).mean():.2%} |")
    report_lines.append(f"| 盈亏比 | {_profit_loss_ratio(valid_t0)} | {_profit_loss_ratio(valid_t1)} | {_profit_loss_ratio(valid_t2)} |")

    # ── 2. 按评分等级统计 ──
    report_lines.append("\n## 2. 按评分等级(S/A/B/C/D)统计\n")

    grade_order = ["S", "A", "B", "C", "D"]
    report_lines.append("| 等级 | 命中次数 | 占比 | T日均值(%) | T日胜率 | T+1均值(%) | T+1胜率 | T+2均值(%) | T+2胜率 |")
    report_lines.append("|------|---------|------|----------|--------|-----------|---------|-----------|---------|")

    for grade in grade_order:
        gdf = df[df["grade"] == grade]
        if len(gdf) == 0:
            report_lines.append(f"| {grade} | 0 | 0% | - | - | - | - | - | - |")
            continue

        gt0 = gdf["ret_t0"].dropna()
        gt1 = gdf["ret_t1"].dropna()
        gt2 = gdf["ret_t2"].dropna()
        pct = len(gdf) / len(df) * 100

        t0_mean = gt0.mean() if len(gt0) > 0 else 0
        t0_wr = (gt0 > 0).mean() if len(gt0) > 0 else 0
        t1_mean = gt1.mean() if len(gt1) > 0 else 0
        t1_wr = (gt1 > 0).mean() if len(gt1) > 0 else 0
        t2_mean = gt2.mean() if len(gt2) > 0 else 0
        t2_wr = (gt2 > 0).mean() if len(gt2) > 0 else 0

        report_lines.append(
            f"| {grade} | {len(gdf)} | {pct:.1f}% | {t0_mean:.4f} | {t0_wr:.2%} | {t1_mean:.4f} | {t1_wr:.2%} | {t2_mean:.4f} | {t2_wr:.2%} |"
        )

    # ── 3. 按定式类型统计 ──
    report_lines.append("\n## 3. 按定式类型统计\n")

    report_lines.append("| 定式类型 | 命中次数 | T日均值(%) | T日胜率 | T+1均值(%) | T+1胜率 | T+2均值(%) | T+2胜率 |")
    report_lines.append("|---------|---------|----------|--------|-----------|---------|-----------|---------|")

    for pt in ["N型起跳", "横盘起跳", "上升波段延续"]:
        pdf = df[df["pattern"] == pt]
        if len(pdf) == 0:
            continue

        pt0 = pdf["ret_t0"].dropna()
        pt1 = pdf["ret_t1"].dropna()
        pt2 = pdf["ret_t2"].dropna()

        report_lines.append(
            f"| {pt} | {len(pdf)} | {pt0.mean():.4f} | {(pt0 > 0).mean():.2%} | {pt1.mean():.4f} | {(pt1 > 0).mean():.2%} | {pt2.mean():.4f} | {(pt2 > 0).mean():.2%} |"
        )

    # ── 4. 按定式类型 x 评分等级交叉统计 ──
    report_lines.append("\n## 4. 定式类型 x 评分等级交叉分析\n")

    for pt in ["N型起跳", "横盘起跳", "上升波段延续"]:
        pdf = df[df["pattern"] == pt]
        if len(pdf) == 0:
            continue

        report_lines.append(f"\n### {pt}\n")
        report_lines.append("| 等级 | 次数 | T日均值(%) | T日胜率 | T+1均值(%) | T+1胜率 | T+2均值(%) | T+2胜率 | 盈亏比(T+1) |")
        report_lines.append("|------|------|----------|--------|-----------|---------|-----------|---------|------------|")

        for grade in grade_order:
            gdf = pdf[pdf["grade"] == grade]
            if len(gdf) == 0:
                continue

            gt0 = gdf["ret_t0"].dropna()
            gt1 = gdf["ret_t1"].dropna()
            gt2 = gdf["ret_t2"].dropna()

            report_lines.append(
                f"| {grade} | {len(gdf)} | {gt0.mean():.4f} | {(gt0 > 0).mean():.2%} | {gt1.mean():.4f} | {(gt1 > 0).mean():.2%} | {gt2.mean():.4f} | {(gt2 > 0).mean():.2%} | {_profit_loss_ratio(gt1)} |"
            )

    # ── 5. 按分数区间统计 ──
    report_lines.append("\n## 5. 按分数区间统计\n")

    bins = [(0, 30), (30, 40), (40, 55), (55, 70), (70, 85), (85, 100)]
    report_lines.append("| 分数区间 | 次数 | T日均值(%) | T日胜率 | T+1均值(%) | T+1胜率 | T+2均值(%) | T+2胜率 |")
    report_lines.append("|---------|------|----------|--------|-----------|---------|-----------|---------|")

    for lo, hi in bins:
        bdf = df[(df["final_score"] >= lo) & (df["final_score"] < hi)]
        if len(bdf) == 0:
            continue

        bt0 = bdf["ret_t0"].dropna()
        bt1 = bdf["ret_t1"].dropna()
        bt2 = bdf["ret_t2"].dropna()

        report_lines.append(
            f"| [{lo},{hi}) | {len(bdf)} | {bt0.mean():.4f} | {(bt0 > 0).mean():.2%} | {bt1.mean():.4f} | {(bt1 > 0).mean():.2%} | {bt2.mean():.4f} | {(bt2 > 0).mean():.2%} |"
        )

    # ── 6. 风险扣分影响分析 ──
    report_lines.append("\n## 6. 风险扣分影响分析\n")

    no_risk = df[df["risk_penalty"] == 0]
    has_risk = df[df["risk_penalty"] < 0]

    report_lines.append("| 组别 | 次数 | T日均值(%) | T日胜率 | T+1均值(%) | T+1胜率 | T+2均值(%) | T+2胜率 |")
    report_lines.append("|------|------|----------|--------|-----------|---------|-----------|---------|")

    for label, sub in [("无风险扣分", no_risk), ("有风险扣分", has_risk)]:
        if len(sub) == 0:
            continue
        st0 = sub["ret_t0"].dropna()
        st1 = sub["ret_t1"].dropna()
        st2 = sub["ret_t2"].dropna()
        report_lines.append(
            f"| {label} | {len(sub)} | {st0.mean():.4f} | {(st0 > 0).mean():.2%} | {st1.mean():.4f} | {(st1 > 0).mean():.2%} | {st2.mean():.4f} | {(st2 > 0).mean():.2%} |"
        )

    # ── 7. MACD辅助评分影响 ──
    report_lines.append("\n## 7. MACD辅助评分影响\n")

    macd_pos = df[df["macd_score"] > 0]
    macd_zero = df[df["macd_score"] == 0]
    macd_neg = df[df["macd_score"] < 0]

    report_lines.append("| MACD评分 | 次数 | T日均值(%) | T日胜率 | T+1均值(%) | T+1胜率 | T+2均值(%) | T+2胜率 |")
    report_lines.append("|---------|------|----------|--------|-----------|---------|-----------|---------|")

    for label, sub in [("正分(利好)", macd_pos), ("零分", macd_zero), ("负分(利空)", macd_neg)]:
        if len(sub) == 0:
            continue
        st0 = sub["ret_t0"].dropna()
        st1 = sub["ret_t1"].dropna()
        st2 = sub["ret_t2"].dropna()
        report_lines.append(
            f"| {label} | {len(sub)} | {st0.mean():.4f} | {(st0 > 0).mean():.2%} | {st1.mean():.4f} | {(st1 > 0).mean():.2%} | {st2.mean():.4f} | {(st2 > 0).mean():.2%} |"
        )

    # ── 8. 月度分布 ──
    report_lines.append("\n## 8. 按月份统计\n")

    df["month"] = df["date"].str[:7]
    monthly = df.groupby("month").agg(
        count=("symbol", "size"),
        t0_mean=("ret_t0", "mean"),
        t0_wr=("ret_t0", lambda x: (x.dropna() > 0).mean() if len(x.dropna()) > 0 else 0),
        t1_mean=("ret_t1", "mean"),
        t1_wr=("ret_t1", lambda x: (x.dropna() > 0).mean() if len(x.dropna()) > 0 else 0),
        t2_mean=("ret_t2", "mean"),
        t2_wr=("ret_t2", lambda x: (x.dropna() > 0).mean() if len(x.dropna()) > 0 else 0),
    ).reset_index()

    report_lines.append("| 月份 | 命中次数 | T日均值(%) | T日胜率 | T+1均值(%) | T+1胜率 | T+2均值(%) | T+2胜率 |")
    report_lines.append("|------|---------|----------|--------|-----------|---------|-----------|---------|")

    for _, row in monthly.iterrows():
        report_lines.append(
            f"| {row['month']} | {row['count']} | {row['t0_mean']:.4f} | {row['t0_wr']:.2%} | {row['t1_mean']:.4f} | {row['t1_wr']:.2%} | {row['t2_mean']:.4f} | {row['t2_wr']:.2%} |"
        )

    # ── 9. 评分各分项与收益的相关性 ──
    report_lines.append("\n## 9. 评分分项与收益相关性\n")

    score_cols = ["final_score", "specific_score", "common_score", "macd_score", "signal_score", "risk_penalty"]
    valid_df0 = df.dropna(subset=["ret_t0"])
    valid_df = df.dropna(subset=["ret_t1"])
    valid_df2 = df.dropna(subset=["ret_t2"])

    report_lines.append("| 评分分项 | 与T日收益相关系数 | 与T+1收益相关系数 | 与T+2收益相关系数 |")
    report_lines.append("|---------|-----------------|-----------------|-----------------|")

    for col in score_cols:
        if col in valid_df.columns and len(valid_df) > 10:
            corr0 = valid_df0[col].corr(valid_df0["ret_t0"]) if len(valid_df0) > 10 else 0
            corr1 = valid_df[col].corr(valid_df["ret_t1"])
            corr2 = valid_df2[col].corr(valid_df2["ret_t2"]) if len(valid_df2) > 10 else 0
            report_lines.append(f"| {col} | {corr0:.4f} | {corr1:.4f} | {corr2:.4f} |")

    # ── 10. 评分分布分析（分数为何偏低） ──
    report_lines.append("\n## 10. 评分分布分析 — 分数为何偏低？\n")

    report_lines.append("### 10.1 各维度得分率\n")
    report_lines.append("| 维度 | 满分 | 平均得分 | 中位数 | 得分率 | 说明 |")
    report_lines.append("|------|------|----------|--------|--------|------|")

    dim_info = [
        ("specific_score", 30, "定式专属"),
        ("common_score", 30, "通用质量"),
        ("macd_score", 25, "MACD环境"),
        ("signal_score", 15, "信号强度"),
    ]
    for col, full, label in dim_info:
        avg = df[col].mean()
        med = df[col].median()
        rate = avg / full * 100
        report_lines.append(f"| {label} | {full} | {avg:.1f} | {med:.1f} | {rate:.1f}% | — |")

    avg_penalty = df["risk_penalty"].mean()
    med_penalty = df["risk_penalty"].median()
    report_lines.append(f"| 风险扣分 | — | {avg_penalty:.1f} | {med_penalty:.1f} | — | 平均拖累 |")

    report_lines.append("\n### 10.2 基础分(扣分前)分布\n")
    df["base_score"] = df["specific_score"] + df["common_score"] + df["macd_score"] + df["signal_score"]
    avg_base = df["base_score"].mean()
    report_lines.append(f"- 平均基础分: {avg_base:.1f}/100")
    report_lines.append(f"- 基础分中位数: {df['base_score'].median():.1f}/100")

    base_bins = [(0, 40), (40, 50), (50, 60), (60, 70), (70, 80), (80, 100)]
    report_lines.append("\n| 基础分段 | 数量 | 占比 |")
    report_lines.append("|----------|------|------|")
    for lo, hi in base_bins:
        cnt = len(df[(df["base_score"] >= lo) & (df["base_score"] < hi)])
        pct = cnt / len(df) * 100 if len(df) > 0 else 0
        report_lines.append(f"| [{lo},{hi}) | {cnt} | {pct:.1f}% |")

    report_lines.append("\n### 10.3 风险扣分统计\n")
    has_penalty = df[df["risk_penalty"] < 0]
    no_penalty = df[df["risk_penalty"] == 0]
    penalty_ratio = len(has_penalty) / len(df) * 100 if len(df) > 0 else 0
    report_lines.append(f"- 被扣分的信号: {len(has_penalty)}/{len(df)} ({penalty_ratio:.1f}%)")
    if len(has_penalty) > 0:
        report_lines.append(f"- 平均扣分: {has_penalty['risk_penalty'].mean():.1f}")
        report_lines.append(f"- 最大扣分: {has_penalty['risk_penalty'].min():.1f}")

    report_lines.append("\n### 10.4 按定式类型的维度得分率\n")
    report_lines.append("| 定式 | 专属(30) | 通用(30) | MACD(25) | 信号(15) | 风险扣分 | 最终均分 |")
    report_lines.append("|------|----------|----------|----------|----------|----------|----------|")
    for pt_name, grp in df.groupby("pattern"):
        sp = grp["specific_score"].mean()
        cm = grp["common_score"].mean()
        mc = grp["macd_score"].mean()
        sg = grp["signal_score"].mean()
        rk = grp["risk_penalty"].mean()
        fs = grp["final_score"].mean()
        report_lines.append(
            f"| {pt_name} | {sp:.1f}({sp/30*100:.0f}%) | {cm:.1f}({cm/30*100:.0f}%) | "
            f"{mc:.1f}({mc/25*100:.0f}%) | {sg:.1f}({sg/15*100:.0f}%) | {rk:.1f} | {fs:.1f} |"
        )

    report_lines.append("\n### 10.5 低分成因诊断\n")
    report_lines.append("根据以上数据，分数偏低的主要原因：\n")

    dim_rates = []
    for col, full, label in dim_info:
        rate = df[col].mean() / full * 100
        dim_rates.append((label, rate, full, df[col].mean()))

    dim_rates.sort(key=lambda x: x[1])
    for i, (label, rate, full, avg) in enumerate(dim_rates):
        if rate < 50:
            report_lines.append(f"{i+1}. **{label}得分率仅 {rate:.0f}%**（平均 {avg:.1f}/{full}）: 该维度内部条件较严，大多数信号难以拿到高分")
        elif rate < 65:
            report_lines.append(f"{i+1}. **{label}得分率 {rate:.0f}%**（平均 {avg:.1f}/{full}）: 得分中等偏低")
        else:
            report_lines.append(f"{i+1}. {label}得分率 {rate:.0f}%（平均 {avg:.1f}/{full}）: 相对正常")

    if penalty_ratio > 40:
        report_lines.append(f"{len(dim_rates)+1}. **风险扣分面过广**（{penalty_ratio:.0f}% 信号被扣分，平均 {avg_penalty:.1f}），严重拉低最终分")
    elif penalty_ratio > 20:
        report_lines.append(f"{len(dim_rates)+1}. 风险扣分影响中等（{penalty_ratio:.0f}% 信号被扣分，平均 {avg_penalty:.1f}）")

    # ── 11. 结论 ──
    report_lines.append("\n## 11. 结论与建议\n")

    overall_t0_wr = (valid_t0 > 0).mean() if len(valid_t0) > 0 else 0
    overall_t0_mean = valid_t0.mean() if len(valid_t0) > 0 else 0
    overall_t1_wr = (valid_t1 > 0).mean() if len(valid_t1) > 0 else 0
    overall_t1_mean = valid_t1.mean() if len(valid_t1) > 0 else 0
    overall_t2_wr = (valid_t2 > 0).mean() if len(valid_t2) > 0 else 0
    overall_t2_mean = valid_t2.mean() if len(valid_t2) > 0 else 0

    report_lines.append("### 评分系统有效性判断\n")

    report_lines.append("#### 整体表现\n")
    report_lines.append(f"- **T日(信号日)**: 胜率 {overall_t0_wr:.2%}, 均值收益 {overall_t0_mean:.4f}%")
    report_lines.append(f"- **T+1日**: 胜率 {overall_t1_wr:.2%}, 均值收益 {overall_t1_mean:.4f}%")
    report_lines.append(f"- **T+2日**: 胜率 {overall_t2_wr:.2%}, 均值收益 {overall_t2_mean:.4f}%")

    report_lines.append("\n#### 各等级对比(T+1)\n")

    s_df = df[df["grade"] == "S"]
    a_df = df[df["grade"] == "A"]
    b_df = df[df["grade"] == "B"]
    c_df = df[df["grade"] == "C"]
    d_df = df[df["grade"] == "D"]

    for label, gdf in [("S", s_df), ("A", a_df), ("B", b_df), ("C", c_df), ("D", d_df)]:
        gt1 = gdf["ret_t1"].dropna()
        wr = (gt1 > 0).mean() if len(gt1) > 0 else 0
        mn = gt1.mean() if len(gt1) > 0 else 0
        report_lines.append(f"- **{label}级**: 胜率 {wr:.2%}, 均值收益 {mn:.4f}%, 样本数 {len(gt1)}")

    s_wr = (s_df["ret_t1"].dropna() > 0).mean() if len(s_df["ret_t1"].dropna()) > 0 else 0
    a_wr = (a_df["ret_t1"].dropna() > 0).mean() if len(a_df["ret_t1"].dropna()) > 0 else 0
    d_wr = (d_df["ret_t1"].dropna() > 0).mean() if len(d_df["ret_t1"].dropna()) > 0 else 0

    s_mean = s_df["ret_t1"].dropna().mean() if len(s_df["ret_t1"].dropna()) > 0 else 0
    a_mean = a_df["ret_t1"].dropna().mean() if len(a_df["ret_t1"].dropna()) > 0 else 0
    d_mean = d_df["ret_t1"].dropna().mean() if len(d_df["ret_t1"].dropna()) > 0 else 0

    report_lines.append("\n#### 有效性结论\n")

    if s_wr > a_wr > d_wr:
        report_lines.append("**结论: 评分等级与胜率呈正相关，高评分等级(S>A>D)对应更高胜率，评分系统有效。**")
    elif s_wr > d_wr:
        report_lines.append("**结论: 评分等级与胜率整体呈正相关(S>D)，但中间等级存在波动，评分系统部分有效。**")
    else:
        report_lines.append("**结论: 评分等级与胜率未呈现明显正相关，评分系统有效性需要重新审视。**")

    monotonic = s_mean >= a_mean >= d_mean
    if monotonic:
        report_lines.append("\n- 均值收益也呈单调递减(S>=A>=D)，评分对收益预测能力强。")
    else:
        report_lines.append("\n- 均值收益未呈单调递减，评分对绝对收益的预测能力有限。")

    report_path = ROOT / "output" / "brick_pattern_backtest_report.md"
    report_path.write_text("\n".join(report_lines), encoding="utf-8")
    print(f"分析报告已保存: {report_path}")


def _profit_loss_ratio(series: pd.Series) -> str:
    """计算盈亏比"""
    wins = series[series > 0]
    losses = series[series < 0]
    if len(losses) == 0 or losses.mean() == 0:
        return "inf" if len(wins) > 0 else "N/A"
    ratio = abs(wins.mean() / losses.mean()) if len(wins) > 0 else 0
    return f"{ratio:.2f}"


if __name__ == "__main__":
    run_backtest()
