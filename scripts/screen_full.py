"""全市场砖形图定式选股 - 完整版（含评分拆解 + 指标数据）"""
import sys, time, sqlite3, json
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd

PROJECT = Path('/opt/data/workspace/trader')
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

db_path = '/opt/data/workspace/trader/db/market.db'
conn = sqlite3.connect(db_path, timeout=30)

target = conn.execute("SELECT MAX(date) FROM stock_daily").fetchone()[0]
print(f"扫描日期: {target}")

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

pattern_cn = {
    "N_SHAPE_JUMP": "N型起跳",
    "SIDEWAYS_JUMP": "横盘起跳",
    "UPTREND_CONTINUE": "上升波段延续",
}

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
    
    if match.final_matched:
        # 计算裸分拆解
        indicators = _calc_indicators(df)
        ptype = PatternType(match.matched_pattern) if match.matched_pattern else PatternType.N_SHAPE_JUMP
        q_score, q_items = compute_common_quality_score(indicators, day_index, ptype)
        m_score, m_items = compute_macd_auxiliary_score(indicators, day_index, ptype)
        s_score, s_items = compute_signal_strength_score(indicators, day_index)
        p_score, p_items = compute_p3_bonus(indicators, day_index, ptype)
        r_penalty, r_items, _ = compute_risk_penalty(indicators, day_index, ptype)
        
        prev_close = float(df['close'].iloc[day_index-1]) if day_index > 0 else float(df['close'].iloc[day_index])
        day_change = (float(df['close'].iloc[day_index]) - prev_close) / prev_close * 100
        vol_30_start = max(0, day_index - 30)
        avg_vol_30 = float(df['volume'].iloc[vol_30_start:day_index+1].mean()) / 10000
        
        is_limit_up = day_change >= 9.5

        # 5日累计涨幅（T3已进评分函数，此处供T4涨停质量与展示用）
        if day_index >= 5 and float(df['close'].iloc[day_index-5]) > 0:
            close_5d_ago = float(df['close'].iloc[day_index-5])
            cum_chg_5d = (float(df['close'].iloc[day_index]) - close_5d_ago) / close_5d_ago * 100
        else:
            cum_chg_5d = 0.0

        # T1 板块动量衰减扣分（作用于最终分 score，不动 raw_total）
        sec_pen, sec_flags = sector_penalty(
            industry, is_limit_up, ind_today, ind_prev, top3_prev)
        base_score = round(match.final_score, 1) if match.final_score else 0
        adjusted_score = round(max(0, base_score + sec_pen), 1)

        # T4 涨停次日独立评估标签（不进 score，仅预警）
        ind_today_chg = ind_today.get(industry, 0.0)
        vol_ratio_val = round(float(df['volume'].iloc[day_index]) / 10000 / max(avg_vol_30, 0.01), 2)
        lu_quality = limit_up_quality(
            vol_ratio_val, float(indicators["brick"][day_index]),
            ind_today_chg, cum_chg_5d) if is_limit_up else None

        # T5 分组：涨停组 / 强势未封板组(3%~9.5%) / 普通组
        if is_limit_up:
            group = "limit_up"
        elif 3.0 <= day_change < 9.5:
            group = "strong_unsealed"
        else:
            group = "normal"

        results.append({
            "symbol": symbol,
            "name": name,
            "industry": industry,
            "pattern": pattern_cn.get(str(match.matched_pattern), "未知"),
            "score": adjusted_score,
            "sector_penalty": sec_pen,
            "sector_flags": sec_flags,
            "limit_up_quality": lu_quality,
            "cum_chg_5d": round(cum_chg_5d, 2),
            "group": group,
            "grade": match.grade or "",
            "close": round(float(df['close'].iloc[day_index]), 2),
            "day_change": round(day_change, 2),
            "is_limit_up": is_limit_up,
            "vol": round(float(df['volume'].iloc[day_index]) / 10000, 2),
            "avg_vol_30": round(avg_vol_30, 2),
            "vol_ratio": round(float(df['volume'].iloc[day_index]) / 10000 / max(avg_vol_30, 0.01), 2),
            "turnover_rate": round(float(df['turnover_rate'].iloc[day_index]), 2) if pd.notna(df['turnover_rate'].iloc[day_index]) else None,
            # 裸分拆解
            "q_score": q_score, "q_items": q_items,
            "m_score": m_score, "m_items": m_items,
            "s_score": s_score, "s_items": s_items,
            "p_score": p_score, "p_items": p_items,
            "r_penalty": r_penalty, "r_items": r_items,
            "raw_total": round(q_score + m_score + s_score + p_score - r_penalty, 1),
            # 关键指标
            "diff": round(float(indicators["macd_diff"][day_index]), 4),
            "dea": round(float(indicators["macd_dea"][day_index]), 4),
            "hist": round(float(indicators["macd_hist"][day_index]), 4),
            "brick_val": round(float(indicators["brick"][day_index]), 4),
            "st_val": round(float(indicators["short_trend"][day_index]), 4),
            "ls_val": round(float(indicators["long_short"][day_index]), 4),
            "open": round(float(df['open'].iloc[day_index]), 2),
            "high": round(float(df['high'].iloc[day_index]), 2),
            "low": round(float(df['low'].iloc[day_index]), 2),
            "prev_close": round(prev_close, 2),
        })
    
    if idx % 500 == 0:
        print(f"  [{idx}/{total}] {len(results)} matches, {time.perf_counter()-t0:.0f}s", flush=True)

# T2 普跌日自动降权：全市场均涨 < -1% 视为普跌日，整体 score×0.85
market_avg = conn.execute(
    "SELECT AVG((d.close - p.close) / p.close * 100) "
    "FROM stock_daily d JOIN stock_daily p ON d.symbol = p.symbol "
    "WHERE d.date = ? AND p.date = ("
    "  SELECT MAX(date) FROM stock_daily WHERE symbol = d.symbol AND date < ?)",
    (target, target),
).fetchone()[0] or 0.0
is_bearish_day = market_avg < -1.0

conn.close()

# 顺序固定：T1 板块扣分已在循环内作用于 score，此处 T2 再对该 score 整体打折
if is_bearish_day:
    for r in results:
        r["score"] = round(r["score"] * 0.85, 1)
        r["regime_flag"] = f"普跌日(均涨{market_avg:.2f}%)-置信度降一档"

results.sort(key=lambda r: r["score"], reverse=True)

# ── 分组配额制 + 精选门槛（改造点）──
# 原实现：先全局 results[:50] 截断，再分组 → 强势优质票可能在贴 T4/T5 标签前被全局排名挤出。
# 现实现：① 先按最终分门槛 MIN_SCORE 砍掉 C/D 级凑数票；
#         ② 再对【全部命中票】按 T5 分组，组内截 TopN 配额，避免某一类被另一类挤光。
# 每条记录的 group / limit_up_quality（T4/T5）已在循环内对所有命中票算好，此处只做过滤+分组+排序。
#
# MIN_SCORE：精选门槛，只保留最终分 ≥ 此值的高确信度标的。
# score≥45 在常规交易日约落 ~10 只，符合"精选"诉求；想更精调高、想更宽调低。
MIN_SCORE = 45.0

# 配额作为兜底：极端放量日命中过多时仍按组截顶，避免单组淹没结果。
GROUP_QUOTA = {
    "limit_up": 6,        # 🔴 涨停组：方向不确定，给适中配额
    "strong_unsealed": 8, # 🟡 强势未封板组：次日方向可信度最高，给最大配额
    "normal": 6,          # ⚪ 普通组
}


def _rank_within_group(rows: list[dict], group_name: str) -> list[dict]:
    """组内排序后截配额。

    涨停组特殊处理：T4 判定为 strong 的优质涨停优先级最高（strong→neutral→weak），
    同质量内再按 score 降序——避免「缩量锁仓的优质涨停」被「放量分歧的高分涨停」挤出。
    其余组直接按 score 降序。
    """
    quota = GROUP_QUOTA.get(group_name, len(rows))
    if group_name == "limit_up":
        quality_rank = {"strong": 0, "neutral": 1, "weak": 2}
        rows = sorted(
            rows,
            key=lambda r: (quality_rank.get(r.get("limit_up_quality"), 1), -r["score"]),
        )
    else:
        rows = sorted(rows, key=lambda r: r["score"], reverse=True)
    return rows[:quota]


# 精选门槛：砍掉最终分 < MIN_SCORE 的 C/D 级凑数票（放在普跌日降权之后，作用于最终调整分）
results = [r for r in results if r["score"] >= MIN_SCORE]

# 先对全部命中票分组（不再先做全局 [:50] 截断）
all_grouped = {
    "limit_up": [r for r in results if r["group"] == "limit_up"],
    "strong_unsealed": [r for r in results if r["group"] == "strong_unsealed"],
    "normal": [r for r in results if r["group"] == "normal"],
}

# 各组内排序 + 截配额
grouped = {
    name: _rank_within_group(rows, name)
    for name, rows in all_grouped.items()
}

# 落盘用的 top_results：合并三组配额结果，统一按 score 降序便于人工浏览
top_results = sorted(
    [r for rows in grouped.values() for r in rows],
    key=lambda r: r["score"],
    reverse=True,
)

# 保存原始结果
output_dir = Path('/opt/data/output/screening_raw')
output_dir.mkdir(parents=True, exist_ok=True)
filename = target.replace('-', '')
with open(output_dir / f'{filename}.json', 'w') as f:
    json.dump({
        "date": target,
        "market_avg": round(market_avg, 2),
        "is_bearish_day": is_bearish_day,
        "group_counts": {k: len(v) for k, v in grouped.items()},
        "results": top_results,
    }, f, ensure_ascii=False, indent=2, default=str)

print(f"\n扫描完成: {total}只 → {len(results)}只命中, {time.perf_counter()-t0:.0f}s")
print(f"原始结果已保存: {output_dir / f'{filename}.json'}")


def _print_group(title, rows, show_quality=False):
    if not rows:
        return
    print(f"\n{title}（{len(rows)}只）")
    header = f"{'代码':<8} {'名称':<10} {'定式':<14} {'评分':<6} {'等级':<4} {'涨跌%':<8} {'成交额(亿)':<10}"
    if show_quality:
        header += " 涨停质量"
    print(header)
    print("-" * (72 + (10 if show_quality else 0)))
    for r in rows:
        line = (f"{r['symbol']:<8} {r['name']:<10} {r['pattern']:<14} "
                f"{r['score']:<6.1f} {r['grade']:<4} {r['day_change']:>+7.2f}% {r['vol']:>9.2f}")
        if show_quality:
            quality = r.get("limit_up_quality")
            if quality == "weak":
                line += "  ⚠️ 弱(次日防高开低走)"
            elif quality == "strong":
                line += "  ✅ 强"
            else:
                line += f"  {quality or ''}"
        print(line)


_print_group("🔴 涨停组（次日方向看 limit_up_quality，不直接信偏多）",
             grouped["limit_up"], show_quality=True)
_print_group("🟡 强势未封板组（3%~9.5%，次日方向可信度高于涨停组）",
             grouped["strong_unsealed"])
_print_group("⚪ 普通组", grouped["normal"][:10])
