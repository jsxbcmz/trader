#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股交易日志绩效分析脚本
=========================
读取券商导出的 CSV，自动分析交易绩效，输出 Markdown 报告 + Matplotlib 图表。

用法：
    python analyze_trades.py --file trades.csv
    python analyze_trades.py --file trades.csv --commission 0.00015 --stamp 0.0005 --output reports/
    python analyze_trades.py --file trades.csv --no-chart --no-file

参数：
    --file          CSV 文件路径（必填）
    --commission    佣金费率（默认 0.00015 即万1.5）
    --stamp         印花税率，卖出时收取（默认 0.0005 即千0.5）
    --transfer      过户费率（默认 0.00001，仅沪市）
    --output        报告输出目录（默认 reports/）
    --no-chart      不生成图表
    --no-file       只打印，不写文件
    --encoding      强制指定编码（默认自动检测 GBK/UTF-8）

依赖：
    pip install pandas matplotlib
"""

import argparse
import sys
import os
from datetime import datetime
from pathlib import Path

try:
    import pandas as pd
except ImportError:
    print("ERROR: 请先安装 pandas: pip install pandas")
    sys.exit(1)

# matplotlib 可选（--no-chart 时不需要）
try:
    import matplotlib
    matplotlib.use("Agg")       # 非交互后端，适合无 GUI 环境
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mtick
    plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "Arial Unicode MS"]
    plt.rcParams["axes.unicode_minus"] = False
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

# ─────────────────────────────────────────────────────────────
# 列名标准化映射
# ─────────────────────────────────────────────────────────────

# 同义列名 → 标准名
COLUMN_ALIASES = {
    "标的代码":   ["证券代码", "股票代码", "代码", "symbol", "code"],
    "标的名称":   ["证券名称", "股票名称", "名称", "name"],
    "买入日期":   ["成交日期", "交易日期", "建仓日期", "date", "buy_date", "entry_date"],
    "买入时间":   ["成交时间", "买入成交时间", "time", "buy_time", "entry_time"],
    "买入价":     ["买入价格", "成交均价(买)", "entryprice", "entry_price", "买价"],
    "卖出日期":   ["平仓日期", "sell_date", "exit_date"],
    "卖出时间":   ["卖出成交时间", "sell_time", "exit_time"],
    "卖出价":     ["卖出价格", "成交均价(卖)", "exitprice", "exit_price", "卖价"],
    "股数":       ["成交数量", "持仓数量", "shares", "quantity", "数量", "买入数量"],
    "盈亏金额":   ["实现盈亏", "盈亏", "pnl", "profit", "net_pnl", "盈亏额"],
    "策略类型":   ["setup", "strategy", "交易策略", "策略"],
}

# 时段划分（开始时间，用于 label）
TIME_SLOTS = [
    ("09:30", "10:00"),
    ("10:00", "10:30"),
    ("10:30", "11:30"),
    ("13:00", "13:30"),
    ("13:30", "14:00"),
    ("14:00", "14:30"),
    ("14:30", "15:00"),
]

# ─────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────

def detect_encoding(path: str) -> str:
    """自动检测文件编码（GBK / UTF-8 / UTF-8-BOM）"""
    with open(path, "rb") as f:
        raw = f.read(4)
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    try:
        with open(path, encoding="utf-8") as f:
            f.read()
        return "utf-8"
    except UnicodeDecodeError:
        return "gbk"


def normalize_columns(df: pd.DataFrame) -> tuple[pd.DataFrame, list]:
    """
    把 df 的列名映射到标准名，返回 (标准化后的 df, 字段检查报告行)
    """
    report = []
    rename_map = {}
    raw_lower = {c.lower().strip(): c for c in df.columns}

    for std, aliases in COLUMN_ALIASES.items():
        # 已有标准名直接跳过
        if std in df.columns:
            continue
        matched = None
        for alias in aliases:
            if alias in df.columns:
                matched = alias
                break
            if alias.lower() in raw_lower:
                matched = raw_lower[alias.lower()]
                break
        if matched:
            rename_map[matched] = std
            report.append(f"  ✓ '{matched}' → 标准名 '{std}'")
        else:
            report.append(f"  ✗ 缺失 '{std}'（尝试过: {aliases[:3]}）")

    df = df.rename(columns=rename_map)
    return df, report


def time_slot_label(t) -> str:
    """将买入时间归入时段，返回标签字符串"""
    if pd.isna(t):
        return "未知时段"
    t_str = str(t).strip()[:5]  # "HH:MM"
    for start, end in TIME_SLOTS:
        if start <= t_str < end:
            return f"{start}-{end}"
    return "其他"


def calc_fees(entry_price, exit_price, shares, commission, stamp, transfer) -> float:
    """计算单笔手续费（买入佣金 + 卖出佣金 + 卖出印花税 + 过户费）"""
    buy_val  = entry_price * shares
    sell_val = exit_price  * shares
    fees = (
        max(buy_val  * commission, 5.0) +   # 买入佣金，最低5元
        max(sell_val * commission, 5.0) +   # 卖出佣金
        sell_val * stamp +                   # 卖出印花税
        (buy_val + sell_val) * transfer      # 过户费（近似，两次均算）
    )
    return round(fees, 2)


def expectancy(wins: pd.Series, losses: pd.Series) -> float:
    """期望值 = 胜率 * 平均盈利 - 败率 * 平均亏损（绝对值）"""
    n = len(wins) + len(losses)
    if n == 0:
        return 0.0
    wr = len(wins) / n
    avg_w = wins.mean() if len(wins) else 0.0
    avg_l = losses.abs().mean() if len(losses) else 0.0
    return round(wr * avg_w - (1 - wr) * avg_l, 2)


# ─────────────────────────────────────────────────────────────
# 核心分析函数
# ─────────────────────────────────────────────────────────────

def analyze(df: pd.DataFrame, commission: float, stamp: float, transfer: float) -> dict:
    """
    对标准化后的 df 进行全维度分析，返回结果字典。
    依赖列：买入价, 卖出价, 股数, 盈亏金额（可选）, 策略类型（可选），买入时间（可选）, 买入日期（可选）
    """
    has_pnl      = "盈亏金额" in df.columns
    has_strategy = "策略类型" in df.columns
    has_time     = "买入时间" in df.columns
    has_date     = "买入日期" in df.columns

    # ── 计算净盈亏（含手续费）────────────────────────────────
    if has_pnl:
        df["净盈亏"] = pd.to_numeric(df["盈亏金额"], errors="coerce").fillna(0)
    elif "买入价" in df.columns and "卖出价" in df.columns and "股数" in df.columns:
        df["买入价"]  = pd.to_numeric(df["买入价"],  errors="coerce")
        df["卖出价"]  = pd.to_numeric(df["卖出价"],  errors="coerce")
        df["股数"]    = pd.to_numeric(df["股数"],    errors="coerce")
        df["净盈亏"]  = (df["卖出价"] - df["买入价"]) * df["股数"]
        df["手续费"]  = df.apply(
            lambda r: calc_fees(r["买入价"], r["卖出价"], r["股数"], commission, stamp, transfer)
            if pd.notna(r["买入价"]) and pd.notna(r["卖出价"]) else 0, axis=1)
        df["净盈亏"] -= df["手续费"]
    else:
        df["净盈亏"] = 0.0

    df["是否盈利"] = df["净盈亏"] > 0
    wins   = df.loc[df["是否盈利"], "净盈亏"]
    losses = df.loc[~df["是否盈利"], "净盈亏"]
    total  = len(df)
    n_win  = len(wins)
    n_lose = len(losses)

    # ── 整体统计 ──────────────────────────────────────────────
    overall = {
        "总交易笔数":   total,
        "盈利次数":     n_win,
        "亏损次数":     n_lose,
        "胜率":         f"{n_win / total * 100:.1f}%" if total else "N/A",
        "平均盈利":     round(wins.mean(), 2) if n_win else 0,
        "平均亏损":     round(losses.mean(), 2) if n_lose else 0,
        "盈亏比":       round(abs(wins.mean() / losses.mean()), 2) if n_win and n_lose and losses.mean() != 0 else "N/A",
        "期望值":       expectancy(wins, losses),
        "总盈亏":       round(df["净盈亏"].sum(), 2),
        "最大单笔盈利": round(df["净盈亏"].max(), 2),
        "最大单笔亏损": round(df["净盈亏"].min(), 2),
        "累计手续费":   round(df["手续费"].sum(), 2) if "手续费" in df else "未计算",
    }

    # ── 按策略类型 ─────────────────────────────────────────────
    by_strategy = {}
    if has_strategy:
        df["策略类型"] = df["策略类型"].fillna("未标注")
        for strat, grp in df.groupby("策略类型"):
            w = grp.loc[grp["是否盈利"], "净盈亏"]
            l = grp.loc[~grp["是否盈利"], "净盈亏"]
            n = len(grp)
            by_strategy[strat] = {
                "笔数": n,
                "胜率": f"{len(w)/n*100:.1f}%" if n else "N/A",
                "平均盈利": round(w.mean(), 2) if len(w) else 0,
                "平均亏损": round(l.mean(), 2) if len(l) else 0,
                "总盈亏":   round(grp["净盈亏"].sum(), 2),
                "期望值":   expectancy(w, l),
            }

    # ── 按交易时段 ─────────────────────────────────────────────
    by_slot = {}
    if has_time:
        df["时段"] = df["买入时间"].apply(time_slot_label)
        for slot, grp in df.groupby("时段"):
            w = grp.loc[grp["是否盈利"], "净盈亏"]
            l = grp.loc[~grp["是否盈利"], "净盈亏"]
            n = len(grp)
            by_slot[slot] = {
                "笔数": n,
                "胜率": f"{len(w)/n*100:.1f}%" if n else "N/A",
                "平均盈利": round(w.mean(), 2) if len(w) else 0,
                "平均亏损": round(l.mean(), 2) if len(l) else 0,
                "总盈亏":   round(grp["净盈亏"].sum(), 2),
            }

    # ── 按星期 ────────────────────────────────────────────────
    by_weekday = {}
    if has_date:
        df["星期"] = pd.to_datetime(df["买入日期"], errors="coerce").dt.day_name()
        day_order = ["Monday","Tuesday","Wednesday","Thursday","Friday"]
        day_cn    = {"Monday":"周一","Tuesday":"周二","Wednesday":"周三","Thursday":"周四","Friday":"周五"}
        for day in day_order:
            grp = df[df["星期"] == day]
            if len(grp) == 0:
                continue
            w = grp.loc[grp["是否盈利"], "净盈亏"]
            l = grp.loc[~grp["是否盈利"], "净盈亏"]
            n = len(grp)
            by_weekday[day_cn[day]] = {
                "笔数": n,
                "胜率": f"{len(w)/n*100:.1f}%" if n else "N/A",
                "总盈亏": round(grp["净盈亏"].sum(), 2),
            }

    # ── 按持仓天数 ────────────────────────────────────────────
    by_holding = {}
    if has_date and "卖出日期" in df.columns:
        df["持仓天数"] = (
            pd.to_datetime(df["卖出日期"], errors="coerce") -
            pd.to_datetime(df["买入日期"], errors="coerce")
        ).dt.days.fillna(1).astype(int)
        bins   = [0, 1, 3, 5, 999]
        labels = ["T+1(次日)", "2-3天", "4-5天", "5天以上"]
        df["持仓区间"] = pd.cut(df["持仓天数"], bins=bins, labels=labels, right=True)
        for interval, grp in df.groupby("持仓区间", observed=True):
            w = grp.loc[grp["是否盈利"], "净盈亏"]
            n = len(grp)
            by_holding[str(interval)] = {
                "笔数": n,
                "胜率": f"{len(w)/n*100:.1f}%" if n else "N/A",
                "总盈亏": round(grp["净盈亏"].sum(), 2),
            }

    # ── 连败后报复交易检测 ────────────────────────────────────
    revenge_trades = []
    if "股数" in df.columns and total >= 4:
        df_sorted = df.reset_index(drop=True)
        avg_shares = df_sorted["股数"].median()
        streak = 0
        for i, row in df_sorted.iterrows():
            if not row["是否盈利"]:
                streak += 1
            else:
                streak = 0
            if streak >= 2 and i + 1 < len(df_sorted):
                next_shares = df_sorted.at[i + 1, "股数"] if pd.notna(df_sorted.at[i + 1, "股数"]) else 0
                if next_shares > avg_shares * 1.5:
                    name = df_sorted.at[i + 1, "标的名称"] if "标的名称" in df_sorted.columns else str(i + 1)
                    revenge_trades.append({
                        "序号":     i + 1,
                        "标的":     name,
                        "连亏笔数": streak,
                        "仓位倍数": round(next_shares / avg_shares, 1),
                    })

    # ── 打板次日溢价率 ─────────────────────────────────────────
    dagao_stats = {}
    if has_strategy and "打板" in df["策略类型"].values and "卖出价" in df.columns and "买入价" in df.columns:
        board = df[df["策略类型"] == "打板"].copy()
        board["次日溢价率"] = (board["卖出价"] - board["买入价"]) / board["买入价"] * 100
        dagao_stats = {
            "打板笔数":       len(board),
            "次日平均溢价率": f"{board['次日溢价率'].mean():.2f}%",
            "正溢价比例":     f"{(board['次日溢价率'] > 0).mean() * 100:.1f}%",
        }

    return {
        "overall":       overall,
        "by_strategy":   by_strategy,
        "by_slot":       by_slot,
        "by_weekday":    by_weekday,
        "by_holding":    by_holding,
        "revenge_trades": revenge_trades,
        "dagao_stats":   dagao_stats,
        "df":            df,
    }


# ─────────────────────────────────────────────────────────────
# 报告渲染
# ─────────────────────────────────────────────────────────────

def render_report(result: dict, col_check: list) -> str:
    """将分析结果转成 Markdown 字符串"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"<!-- generated: {now} -->",
        f"# A股交易绩效分析报告 — {now}",
        "",
        "---",
        "",
        "## 字段标准化报告",
        "",
        "```",
    ] + col_check + [
        "```",
        "",
        "---",
        "",
        "## 一、整体统计",
        "",
        "| 指标 | 值 |",
        "| ---- | -- |",
    ]
    for k, v in result["overall"].items():
        lines.append(f"| {k} | {v} |")

    # ── 策略 ──
    if result["by_strategy"]:
        lines += ["", "## 二、按策略类型", "",
                  "| 策略 | 笔数 | 胜率 | 平均盈利 | 平均亏损 | 总盈亏 | 期望值 |",
                  "| ---- | ---- | ---- | -------- | -------- | ------ | ------ |"]
        for strat, s in sorted(result["by_strategy"].items(), key=lambda x: -abs(x[1]["总盈亏"])):
            lines.append(f"| {strat} | {s['笔数']} | {s['胜率']} | {s['平均盈利']} | {s['平均亏损']} | {s['总盈亏']} | {s['期望值']} |")

    # ── 时段 ──
    if result["by_slot"]:
        lines += ["", "## 三、按交易时段", "",
                  "| 时段 | 笔数 | 胜率 | 平均盈利 | 平均亏损 | 总盈亏 |",
                  "| ---- | ---- | ---- | -------- | -------- | ------ |"]
        for slot in [f"{s}-{e}" for s, e in TIME_SLOTS] + ["其他", "未知时段"]:
            s = result["by_slot"].get(slot)
            if s:
                lines.append(f"| {slot} | {s['笔数']} | {s['胜率']} | {s['平均盈利']} | {s['平均亏损']} | {s['总盈亏']} |")

    # ── 星期 ──
    if result["by_weekday"]:
        lines += ["", "## 四、按星期", "",
                  "| 星期 | 笔数 | 胜率 | 总盈亏 |",
                  "| ---- | ---- | ---- | ------ |"]
        for day, s in result["by_weekday"].items():
            lines.append(f"| {day} | {s['笔数']} | {s['胜率']} | {s['总盈亏']} |")

    # ── 持仓天数 ──
    if result["by_holding"]:
        lines += ["", "## 五、按持仓天数", "",
                  "| 持仓区间 | 笔数 | 胜率 | 总盈亏 |",
                  "| -------- | ---- | ---- | ------ |"]
        for interval, s in result["by_holding"].items():
            lines.append(f"| {interval} | {s['笔数']} | {s['胜率']} | {s['总盈亏']} |")

    # ── 报复交易 ──
    lines += ["", "## 六、连败后报复交易检测", ""]
    if result["revenge_trades"]:
        lines += ["| 序号 | 标的 | 连亏笔数 | 仓位倍数（vs 中位数） |",
                  "| ---- | ---- | -------- | -------------------- |"]
        for t in result["revenge_trades"]:
            lines.append(f"| {t['序号']} | {t['标的']} | {t['连亏笔数']} | {t['仓位倍数']}x |")
        lines.append("\n> ⚠️ 检测到连败后仓位放大超过 1.5x，存在报复交易风险。")
    else:
        lines.append("未检测到明显的连败后报复交易行为。")

    # ── 打板溢价 ──
    if result["dagao_stats"]:
        lines += ["", "## 七、打板次日溢价率", ""]
        for k, v in result["dagao_stats"].items():
            lines.append(f"- {k}：{v}")

    lines += ["", "---", "", "> 本报告由脚本自动生成，仅用于辅助分析，不构成投资建议。"]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# 图表生成
# ─────────────────────────────────────────────────────────────

def make_charts(result: dict, out_dir: Path) -> list:
    """生成并保存图表，返回已保存的文件路径列表"""
    if not HAS_MPL:
        print("WARNING: matplotlib 未安装，跳过图表生成。")
        return []
    saved = []
    df = result["df"]

    fig, axes = plt.subplots(1, 3, figsize=(18, 5))
    fig.suptitle("A股交易绩效分析", fontsize=14)

    # 图1: 累计盈亏曲线
    ax1 = axes[0]
    cumsum = df["净盈亏"].cumsum()
    ax1.plot(range(len(cumsum)), cumsum, color="steelblue", linewidth=1.5)
    ax1.axhline(0, color="red", linestyle="--", linewidth=0.8)
    ax1.set_title("累计盈亏曲线")
    ax1.set_xlabel("交易笔数")
    ax1.set_ylabel("累计盈亏（元）")
    ax1.yaxis.set_major_formatter(mtick.FuncFormatter(lambda x, _: f"{x:,.0f}"))

    # 图2: 按策略盈亏柱状图
    ax2 = axes[1]
    if result["by_strategy"]:
        strats = list(result["by_strategy"].keys())
        pnls   = [result["by_strategy"][s]["总盈亏"] for s in strats]
        colors = ["green" if v >= 0 else "red" for v in pnls]
        ax2.bar(strats, pnls, color=colors)
        ax2.set_title("各策略总盈亏")
        ax2.set_ylabel("总盈亏（元）")
        ax2.tick_params(axis="x", rotation=30)
        ax2.yaxis.set_major_formatter(mtick.FuncFormatter(lambda x, _: f"{x:,.0f}"))
    else:
        ax2.text(0.5, 0.5, "无策略标注数据", ha="center", va="center")
        ax2.set_title("各策略总盈亏")

    # 图3: 时段胜率热力图（简化为水平条形图）
    ax3 = axes[2]
    if result["by_slot"]:
        slots = [f"{s}-{e}" for s, e in TIME_SLOTS if f"{s}-{e}" in result["by_slot"]]
        win_rates = [float(result["by_slot"][s]["胜率"].replace("%", "")) for s in slots]
        bar_colors = ["green" if w >= 50 else "red" for w in win_rates]
        ax3.barh(slots, win_rates, color=bar_colors)
        ax3.axvline(50, color="gray", linestyle="--", linewidth=0.8)
        ax3.set_xlim(0, 100)
        ax3.set_title("各时段胜率")
        ax3.set_xlabel("胜率（%）")
    else:
        ax3.text(0.5, 0.5, "无时段数据（需买入时间列）", ha="center", va="center")
        ax3.set_title("各时段胜率")

    plt.tight_layout()
    chart_path = out_dir / "trades_analysis.png"
    plt.savefig(chart_path, dpi=150, bbox_inches="tight")
    plt.close()
    saved.append(str(chart_path))
    return saved


# ─────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="A股交易日志绩效分析",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--file",       required=True,      help="CSV 文件路径")
    parser.add_argument("--commission", type=float, default=0.00015, help="佣金费率（默认万1.5）")
    parser.add_argument("--stamp",      type=float, default=0.0005,  help="印花税率（默认千0.5）")
    parser.add_argument("--transfer",   type=float, default=0.00001, help="过户费率（默认0.001%%）")
    parser.add_argument("--output",     default="reports",  help="输出目录（默认 reports/）")
    parser.add_argument("--no-chart",   action="store_true",help="不生成图表")
    parser.add_argument("--no-file",    action="store_true",help="只打印，不写文件")
    parser.add_argument("--encoding",   default=None,       help="强制指定 CSV 编码")
    args = parser.parse_args()

    # ── 读取文件 ──
    enc = args.encoding or detect_encoding(args.file)
    print(f"[INFO] 使用编码: {enc}")
    try:
        df = pd.read_csv(args.file, encoding=enc, dtype=str, on_bad_lines="skip")
    except Exception as e:
        print(f"ERROR: 读取 CSV 失败: {e}")
        sys.exit(1)
    print(f"[INFO] 读取到 {len(df)} 行，{len(df.columns)} 列")

    # ── 列名标准化 ──
    df, col_check = normalize_columns(df)
    col_check_str = ["字段标准化检查："] + col_check
    for line in col_check_str:
        print(line)

    # 检查关键列
    required = ["买入价", "卖出价", "股数"]
    has_required = all(c in df.columns for c in required)
    has_pnl = "盈亏金额" in df.columns
    if not has_required and not has_pnl:
        print("ERROR: 缺少必要列（买入价/卖出价/股数 或 盈亏金额），无法继续分析。")
        sys.exit(1)

    # ── 分析 ──
    result = analyze(df, args.commission, args.stamp, args.transfer)

    # ── 输出报告 ──
    report = render_report(result, col_check_str)
    print("\n" + "─" * 60)
    print(report)
    print("─" * 60 + "\n")

    out_dir = Path(args.output)
    saved_files = []

    if not args.no_file:
        out_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime("%Y-%m-%d")
        report_path = out_dir / f"{date_str}_绩效分析.md"
        counter = 2
        while report_path.exists():
            report_path = out_dir / f"{date_str}_绩效分析_{counter}.md"
            counter += 1
        report_path.write_text(report, encoding="utf-8")
        saved_files.append(str(report_path))
        print(f"报告已保存：{report_path}")

    if not args.no_chart:
        charts = make_charts(result, out_dir if not args.no_file else Path("."))
        saved_files.extend(charts)
        for c in charts:
            print(f"图表已保存：{c}")

    if not saved_files:
        print("（仅打印模式，未写入文件）")


if __name__ == "__main__":
    main()
