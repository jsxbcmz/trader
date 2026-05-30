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
from core.models.brick_pattern import PatternType

db_path = '/opt/data/workspace/trader/db/market.db'
conn = sqlite3.connect(db_path, timeout=30)

target = conn.execute("SELECT MAX(date) FROM stock_daily").fetchone()[0]
print(f"扫描日期: {target}")

stock_rows = conn.execute(
    "SELECT sl.symbol, sl.name FROM stock_list sl "
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

for idx, (symbol, name) in enumerate(stock_rows, 1):
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
        
        results.append({
            "symbol": symbol,
            "name": name,
            "pattern": pattern_cn.get(str(match.matched_pattern), "未知"),
            "score": round(match.final_score, 1) if match.final_score else 0,
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

conn.close()
results.sort(key=lambda r: r["score"], reverse=True)

# 保存原始结果
output_dir = Path('/opt/data/output/screening_raw')
output_dir.mkdir(parents=True, exist_ok=True)
filename = target.replace('-', '')
with open(output_dir / f'{filename}.json', 'w') as f:
    json.dump({"date": target, "results": results[:50]}, f, ensure_ascii=False, indent=2, default=str)

print(f"\n扫描完成: {total}只 → {len(results)}只命中, {time.perf_counter()-t0:.0f}s")
print(f"原始结果已保存: {output_dir / f'{filename}.json'}")

# 打印 Top 20
print(f"\n{'代码':<8} {'名称':<10} {'定式':<14} {'评分':<6} {'等级':<4} {'涨跌%':<8} {'成交额(亿)':<10}")
print("-" * 72)
for r in results[:20]:
    print(f"{r['symbol']:<8} {r['name']:<10} {r['pattern']:<14} {r['score']:<6.1f} {r['grade']:<4} {r['day_change']:>+7.2f}% {r['vol']:>9.2f}")
