"""本地版每日选股 + 次日预测 —— 复用改造后的分组配额逻辑。

与 screen_full.py 的唯一区别：路径指向本地仓库根目录，而非生产 /opt/data。
执行流程：
  1. 全市场扫描（三定式 + V4 评分 + 风险扣分 + P3 加分）
  2. 后处理（T1 板块扣分 / T2 普跌日降权 / T4 涨停质量 / T5 分组配额）
  3. 对入选票做次日方向预测（基于定式 + T4 质量 + 板块）
  4. 输出单个 Markdown 结果文档
"""
import sys
import time
import sqlite3
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from core.screening.brick_pattern import screen_single_stock
from core.screening.brick_pattern.helpers import _calc_indicators
from core.screening.brick_pattern.scoring import (
    compute_common_quality_score,
    compute_macd_auxiliary_score,
    compute_signal_strength_score,
    compute_p3_bonus,
)
from core.screening.brick_pattern.scoring_risk import compute_risk_penalty
from core.screening.brick_pattern.postprocess import (
    build_industry_perf,
    sector_penalty,
    limit_up_quality,
)
from core.models.brick_pattern import PatternType
from core.indicators.algorithms import compute_didi_indicator

DB_PATH = PROJECT / "db" / "market.db"

# 精选阈值：只保留最终分 ≥ MIN_SCORE 的高确信度标的（砍掉一堆 C/D 级凑数票）。
# score≥45 在常规交易日约落 ~10 只，符合"精选"诉求。
MIN_SCORE = 45.0

# 配额作为兜底：极端放量日命中过多时，仍按组截顶，避免单组淹没结果。
GROUP_QUOTA = {"limit_up": 6, "strong_unsealed": 8, "normal": 6}

# 注意：PatternType 枚举的 value 本身就是中文（如 N_SHAPE_JUMP = "N型起跳"），
# match.matched_pattern 返回的即是中文名，直接使用即可。


def scan(conn, target):
    prev_date = conn.execute(
        "SELECT MAX(date) FROM stock_daily WHERE date < ?", (target,)
    ).fetchone()[0]
    ind_today, ind_prev, top3_prev = build_industry_perf(conn, target, prev_date)

    stock_rows = conn.execute(
        "SELECT sl.symbol, sl.name, sl.industry FROM stock_list sl "
        "WHERE sl.symbol IN (SELECT DISTINCT symbol FROM stock_daily) "
        "ORDER BY sl.symbol"
    ).fetchall()

    enabled_patterns = (
        PatternType.N_SHAPE_JUMP,
        PatternType.SIDEWAYS_JUMP,
        PatternType.UPTREND_CONTINUE,
    )

    results = []
    t0 = time.perf_counter()
    total = len(stock_rows)

    for idx, (symbol, name, industry) in enumerate(stock_rows, 1):
        df = pd.read_sql_query(
            "SELECT date, open, close, high, low, volume, turnover_rate "
            "FROM stock_daily WHERE symbol = ? ORDER BY date",
            conn, params=(symbol,)
        )
        if df.empty or len(df) < 10:
            continue
        df["date"] = pd.to_datetime(df["date"])
        for c in ["open", "high", "low", "close", "volume", "turnover_rate"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["date", "open", "high", "low", "close"]).reset_index(drop=True)
        dates = df["date"].dt.strftime("%Y-%m-%d")
        if target not in dates.values:
            continue
        day_index = int(dates[dates == target].index[0])

        match = screen_single_stock(
            df=df, index=day_index, symbol=symbol, name=name,
            target_date=target, actual_date=target,
            enabled_patterns=enabled_patterns,
        )

        if not match.final_matched:
            continue

        indicators = _calc_indicators(df)
        ptype = PatternType(match.matched_pattern) if match.matched_pattern else PatternType.N_SHAPE_JUMP

        # 滴滴战法（地铁战法）：判断当天是否处于上车/下车位置
        didi = compute_didi_indicator(
            df["open"].to_numpy(float), df["high"].to_numpy(float),
            df["low"].to_numpy(float), df["close"].to_numpy(float),
        )
        didi_on_board = bool(didi["buy"][day_index])   # 上车点（买）
        didi_off_board = bool(didi["sell"][day_index])  # 下车点（卖）

        prev_close = float(df['close'].iloc[day_index - 1]) if day_index > 0 else float(df['close'].iloc[day_index])
        day_change = (float(df['close'].iloc[day_index]) - prev_close) / prev_close * 100
        vol_30_start = max(0, day_index - 30)
        avg_vol_30 = float(df['volume'].iloc[vol_30_start:day_index + 1].mean()) / 10000
        is_limit_up = day_change >= 9.5

        if day_index >= 5 and float(df['close'].iloc[day_index - 5]) > 0:
            close_5d_ago = float(df['close'].iloc[day_index - 5])
            cum_chg_5d = (float(df['close'].iloc[day_index]) - close_5d_ago) / close_5d_ago * 100
        else:
            cum_chg_5d = 0.0

        sec_pen, sec_flags = sector_penalty(industry, is_limit_up, ind_today, ind_prev, top3_prev)
        base_score = round(match.final_score, 1) if match.final_score else 0
        adjusted_score = round(max(0, base_score + sec_pen), 1)

        ind_today_chg = ind_today.get(industry, 0.0)
        vol_ratio_val = round(float(df['volume'].iloc[day_index]) / 10000 / max(avg_vol_30, 0.01), 2)
        lu_quality = limit_up_quality(
            vol_ratio_val, float(indicators["brick"][day_index]),
            ind_today_chg, cum_chg_5d) if is_limit_up else None

        if is_limit_up:
            group = "limit_up"
        elif 3.0 <= day_change < 9.5:
            group = "strong_unsealed"
        else:
            group = "normal"

        results.append({
            "symbol": symbol, "name": name, "industry": industry,
            "pattern": str(match.matched_pattern) if match.matched_pattern else "未知",
            "score": adjusted_score, "sector_penalty": sec_pen, "sector_flags": sec_flags,
            "limit_up_quality": lu_quality, "cum_chg_5d": round(cum_chg_5d, 2),
            "group": group, "grade": match.grade or "",
            "close": round(float(df['close'].iloc[day_index]), 2),
            "day_change": round(day_change, 2), "is_limit_up": is_limit_up,
            "vol": round(float(df['volume'].iloc[day_index]) / 10000, 2),
            "vol_ratio": vol_ratio_val,
            "ind_today_chg": round(ind_today_chg, 2),
            "brick_val": round(float(indicators["brick"][day_index]), 2),
            "kdj_j": round(float(indicators["kdj_j"][day_index]), 1),
            "didi_on_board": didi_on_board,
            "didi_off_board": didi_off_board,
        })

        if idx % 500 == 0:
            print(f"  [{idx}/{total}] {len(results)} matches, {time.perf_counter()-t0:.0f}s", flush=True)

    return results, total, time.perf_counter() - t0


def apply_postprocess(conn, target, results):
    market_avg = conn.execute(
        "SELECT AVG((d.close - p.close) / p.close * 100) "
        "FROM stock_daily d JOIN stock_daily p ON d.symbol = p.symbol "
        "WHERE d.date = ? AND p.date = ("
        "  SELECT MAX(date) FROM stock_daily WHERE symbol = d.symbol AND date < ?)",
        (target, target),
    ).fetchone()[0] or 0.0
    is_bearish_day = market_avg < -1.0

    if is_bearish_day:
        for r in results:
            r["score"] = round(r["score"] * 0.85, 1)
            r["regime_flag"] = f"普跌日(均涨{market_avg:.2f}%)-置信度降一档"

    # ── 精选门槛：砍掉最终分 < MIN_SCORE 的 C/D 级凑数票 ──
    # 注意：放在普跌日降权之后，门槛作用于最终调整分，保证精选标准在不同行情下一致。
    qualified = [r for r in results if r["score"] >= MIN_SCORE]
    qualified.sort(key=lambda r: r["score"], reverse=True)
    results = qualified

    def rank_within_group(rows, group_name):
        quota = GROUP_QUOTA.get(group_name, len(rows))
        if group_name == "limit_up":
            quality_rank = {"strong": 0, "neutral": 1, "weak": 2}
            rows = sorted(rows, key=lambda r: (quality_rank.get(r.get("limit_up_quality"), 1), -r["score"]))
        else:
            rows = sorted(rows, key=lambda r: r["score"], reverse=True)
        return rows[:quota]

    all_grouped = {
        "limit_up": [r for r in results if r["group"] == "limit_up"],
        "strong_unsealed": [r for r in results if r["group"] == "strong_unsealed"],
        "normal": [r for r in results if r["group"] == "normal"],
    }
    grouped = {name: rank_within_group(rows, name) for name, rows in all_grouped.items()}
    return market_avg, is_bearish_day, grouped


def predict_direction(r):
    """次日方向预测：综合定式、T4 涨停质量、板块、5日累计涨幅、KDJ。

    返回 (方向, 置信度, 理由)。方向 ∈ {偏多, 谨慎偏多, 中性偏震荡, 谨慎}
    """
    reasons = []
    score = 0  # >0 偏多, <0 谨慎

    # 定式基准
    if r["pattern"] == "横盘起跳":
        score += 2
        reasons.append("横盘起跳次日方向可信度最高")
    elif r["pattern"] == "N型起跳":
        score += 1
        reasons.append("N型起跳为反转第二脚")
    else:
        score += 0
        reasons.append("上升波段延续需防高位回落")

    # T4 涨停质量
    if r["is_limit_up"]:
        q = r.get("limit_up_quality")
        if q == "strong":
            score += 1
            reasons.append("涨停质量强(缩量锁仓)")
        elif q == "weak":
            score -= 2
            reasons.append("涨停质量弱(放量分歧/高位),防高开低走")
        else:
            reasons.append("涨停质量中性,方向不直接信")

    # 板块共振
    if r["ind_today_chg"] > 1:
        score += 1
        reasons.append(f"板块今日共振(+{r['ind_today_chg']}%)")
    elif r["ind_today_chg"] < -2:
        score -= 1
        reasons.append(f"逆板块(板块{r['ind_today_chg']}%)")

    # 5日累计透支
    if r["cum_chg_5d"] > 20:
        score -= 2
        reasons.append(f"5日累计涨{r['cum_chg_5d']}%已透支")
    elif r["cum_chg_5d"] < 10:
        score += 1
        reasons.append("处于起涨段未透支")

    # KDJ 超买
    if r["kdj_j"] > 95:
        score -= 1
        reasons.append(f"J值{r['kdj_j']}超买")

    # 板块扣分 flag
    if r["sector_flags"]:
        score -= 1
        reasons.append("; ".join(r["sector_flags"]))

    if score >= 3:
        direction, conf = "偏多", "高"
    elif score >= 1:
        direction, conf = "谨慎偏多", "中"
    elif score >= -1:
        direction, conf = "中性偏震荡", "中"
    else:
        direction, conf = "谨慎", "低"

    return direction, conf, "；".join(reasons)


def didi_signal_tag(r):
    """返回滴滴战法上/下车点的醒目标注（不改变方向打分，仅作位置提示）。

    无信号返回空串。命中时用加粗徽章突出显示。
    """
    if r.get("didi_on_board"):
        return "🟡 **上车点**"
    if r.get("didi_off_board"):
        return "🔵 **下车点**"
    return ""


def build_md(target, market_avg, is_bearish_day, grouped, total, elapsed):
    matched = sum(len(v) for v in grouped.values())
    lines = []
    lines.append(f"# 每日选股分析报告 — {target}")
    lines.append("")
    lines.append(f"> 📌 来源: 🔄自动选股（本地执行 run_screen_local.py，分组配额制 V2，精选门槛 score≥{MIN_SCORE:.0f}）")
    lines.append("")
    lines.append("## 一、大盘环境")
    lines.append("")
    regime = "普跌日（置信度降一档，score×0.85）" if is_bearish_day else "非普跌日"
    lines.append(f"- **扫描日期**：{target}")
    lines.append(f"- **全市场均涨跌**：{market_avg:+.2f}%（{regime}）")
    lines.append(f"- **扫描总数**：{total} 只 → 命中后精选 **{matched} 只**（耗时 {elapsed:.0f}s）")
    lines.append(f"- **精选门槛**：最终评分 ≥ {MIN_SCORE:.0f}（已过滤 C/D 级低确信度凑数票）")
    lines.append("")

    group_meta = [
        ("limit_up", "🔴 涨停组", "次日方向看涨停质量，不直接信偏多", True),
        ("strong_unsealed", "🟡 强势未封板组（3%~9.5%）", "次日方向可信度最高", False),
        ("normal", "⚪ 普通组", "常规候选", False),
    ]

    lines.append("## 二、入选名单（分组配额制）")
    lines.append("")

    for gkey, gtitle, gnote, show_q in group_meta:
        rows = grouped.get(gkey, [])
        if not rows:
            continue
        lines.append(f"### {gtitle} — {len(rows)} 只")
        lines.append(f"> {gnote}")
        lines.append("")
        if show_q:
            header = "| 代码 | 名称 | 定式 | 评分 | 等级 | 涨跌% | 量比 | 涨停质量 | 板块 |"
            sep = "|---|---|---|---|---|---|---|---|---|"
        else:
            header = "| 代码 | 名称 | 定式 | 评分 | 等级 | 涨跌% | 量比 | 板块今日 |"
            sep = "|---|---|---|---|---|---|---|---|"
        lines.append(header)
        lines.append(sep)
        for r in rows:
            if show_q:
                q = r.get("limit_up_quality") or "neutral"
                qcn = {"strong": "✅强", "weak": "⚠️弱", "neutral": "中性"}.get(q, q)
                lines.append(
                    f"| {r['symbol']} | {r['name']} | {r['pattern']} | {r['score']:.1f} | "
                    f"{r['grade']} | {r['day_change']:+.2f}% | {r['vol_ratio']} | {qcn} | {r['industry']} |"
                )
            else:
                lines.append(
                    f"| {r['symbol']} | {r['name']} | {r['pattern']} | {r['score']:.1f} | "
                    f"{r['grade']} | {r['day_change']:+.2f}% | {r['vol_ratio']} | {r['ind_today_chg']:+.2f}% |"
                )
        lines.append("")

    lines.append("## 三、次日方向预测")
    lines.append("")
    lines.append("> 综合「定式 + 涨停质量(T4) + 板块共振 + 5日透支度 + KDJ」给出方向与置信度，仅供参考。")
    lines.append("> 「滴滴」列标注当天是否命中滴滴战法（地铁战法）上/下车点，仅作位置提示，不改变方向打分。")
    lines.append("")
    lines.append("| 代码 | 名称 | 定式 | 评分 | 预测方向 | 置信度 | 滴滴 | 主要依据 |")
    lines.append("|---|---|---|---|---|---|---|---|")

    all_rows = sorted(
        [r for rows in grouped.values() for r in rows],
        key=lambda r: r["score"], reverse=True,
    )
    for r in all_rows:
        direction, conf, reason = predict_direction(r)
        didi_tag = didi_signal_tag(r)
        hit_didi = bool(didi_tag)
        if hit_didi:
            reason = f"{reason}；{didi_tag}"
        r["_pred"] = {"direction": direction, "confidence": conf, "reason": reason}
        # 命中滴滴的行整行突出：行首加 🚇、代码与名称加粗
        if hit_didi:
            lines.append(
                f"| 🚇 **{r['symbol']}** | **{r['name']}** | {r['pattern']} | {r['score']:.1f} | "
                f"**{direction}** | {conf} | {didi_tag} | {reason} |"
            )
        else:
            lines.append(
                f"| {r['symbol']} | {r['name']} | {r['pattern']} | {r['score']:.1f} | "
                f"**{direction}** | {conf} | — | {reason} |"
            )
    lines.append("")

    # 滴滴命中汇总提示（仅在有命中时显示，醒目强调）
    didi_hits = [r for r in all_rows if r.get("didi_on_board") or r.get("didi_off_board")]
    if didi_hits:
        on_names = [r["name"] for r in didi_hits if r.get("didi_on_board")]
        off_names = [r["name"] for r in didi_hits if r.get("didi_off_board")]
        lines.append(f"> 🚇 **滴滴战法命中 {len(didi_hits)} 只**：")
        if on_names:
            lines.append(f"> - 🟡 **上车点（买入位置）**：{'、'.join(on_names)}")
        if off_names:
            lines.append(f"> - 🔵 **下车点（卖出位置）**：{'、'.join(off_names)}")
        lines.append("")

    # 预测方向汇总
    dir_count = {}
    for r in all_rows:
        d = r["_pred"]["direction"]
        dir_count[d] = dir_count.get(d, 0) + 1
    lines.append("### 方向分布")
    lines.append("")
    for d, c in sorted(dir_count.items(), key=lambda x: -x[1]):
        lines.append(f"- **{d}**：{c} 只")
    lines.append("")

    lines.append("## 四、操作提示")
    lines.append("")
    lines.append("- 🟡 强势未封板组次日方向可信度最高，优先关注其中「谨慎偏多/偏多」标的。")
    lines.append("- 🔴 涨停组以「✅强」质量为先，「⚠️弱」防次日高开低走。")
    lines.append("- 凡命中板块扣分 flag（V反风险/逆板块涨停）或 5 日透支的，次日仓位从严。")
    if is_bearish_day:
        lines.append("- ⚠️ 今日为普跌日，所有评分已降一档，整体仓位建议偏轻。")
    lines.append("")
    lines.append(f"---\n*生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")

    return "\n".join(lines)


def main():
    target_arg = sys.argv[1] if len(sys.argv) > 1 else None
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    target = target_arg or conn.execute("SELECT MAX(date) FROM stock_daily").fetchone()[0]
    print(f"扫描日期: {target}")

    results, total, elapsed = scan(conn, target)
    market_avg, is_bearish_day, grouped = apply_postprocess(conn, target, results)
    conn.close()

    md = build_md(target, market_avg, is_bearish_day, grouped, total, elapsed)

    out_dir = PROJECT / "output" / "screening_analysis"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{target}.md"
    out_path.write_text(md, encoding="utf-8")

    matched = sum(len(v) for v in grouped.values())
    print(f"\n扫描完成: {total}只 → {matched}只入选, {elapsed:.0f}s")
    print(f"结果文档已生成: {out_path}")


if __name__ == "__main__":
    main()
