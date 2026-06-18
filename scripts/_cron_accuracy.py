#!/usr/bin/env python3
"""Calculate accuracy of previous day's predictions vs today's actual results."""
import json, sqlite3, sys, os

pred_file = sys.argv[1] if len(sys.argv) > 1 else "/opt/data/output/screening_predictions/2026-06-17.json"
target_date = sys.argv[2] if len(sys.argv) > 2 else "2026-06-18"

with open(pred_file) as f:
    data = json.load(f)

stocks = data["stocks"]
symbols = [s["symbol"] for s in stocks]

conn = sqlite3.connect("db/market.db")
cur = conn.cursor()

# Get today's data for these symbols
placeholders = ",".join(["?"] * len(symbols))
cur.execute(f"""
    SELECT symbol, open, close, pre_close, pct_chg 
    FROM stock_daily 
    WHERE symbol IN ({placeholders}) AND date = ?
""", symbols + [target_date])
rows = {r[0]: r for r in cur.fetchall()}
conn.close()

auto_correct = 0
auto_wrong = 0
manual_correct = 0
manual_wrong = 0
details = []

for s in stocks:
    sym = s["symbol"]
    row = rows.get(sym)
    if not row:
        details.append(f"{sym} {s['name']}: 无今日数据")
        continue
    
    _, open_p, close_p, pre_close_p, pct_chg = row
    if pct_chg is None or pct_chg == 0:
        pct = 0
    else:
        pct = float(pct_chg)
    
    pred = s["pred_direction"]
    source = s.get("source", "auto")
    
    # Determine actual direction
    if pct > 1.5:
        actual = "偏多"
    elif pct > 0.3:
        actual = "震荡偏多"
    elif pct > -0.3:
        actual = "震荡"
    elif pct > -1.5:
        actual = "震荡偏空"
    else:
        actual = "偏空"
    
    # Check if prediction matches
    # "偏多" matches "偏多" or "震荡偏多"
    # "震荡偏多" matches "震荡偏多" or "偏多"
    # "震荡" matches anything close
    # "震荡偏空" matches "震荡偏空" or "偏空"
    is_correct = False
    if pred == "偏多" and actual in ("偏多", "震荡偏多"):
        is_correct = True
    elif pred == "震荡偏多" and actual in ("偏多", "震荡偏多", "震荡"):
        is_correct = True
    elif pred == "震荡" and actual in ("震荡偏多", "震荡", "震荡偏空"):
        is_correct = True
    elif pred == "震荡偏空" and actual in ("震荡", "震荡偏空", "偏空"):
        is_correct = True
    elif pred == "偏空" and actual in ("震荡偏空", "偏空"):
        is_correct = True
    
    if source == "auto":
        if is_correct:
            auto_correct += 1
        else:
            auto_wrong += 1
    else:
        if is_correct:
            manual_correct += 1
        else:
            manual_wrong += 1
    
    details.append(f"{'✅' if is_correct else '❌'} {sym} {s['name']}: 预期{pred} → 实际{actual}({pct:+.2f}%)")

print(f"=== 准确率统计 ===")
print(f"自动: {auto_correct}/{auto_correct+auto_wrong} = {auto_correct/(auto_correct+auto_wrong)*100:.0f}%" if (auto_correct+auto_wrong) > 0 else "自动: 无数据")
print(f"手动: {manual_correct}/{manual_correct+manual_wrong} = {manual_correct/(manual_correct+manual_wrong)*100:.0f}%" if (manual_correct+manual_wrong) > 0 else "手动: 无数据")
total_correct = auto_correct + manual_correct
total = auto_correct + auto_wrong + manual_correct + manual_wrong
print(f"合计: {total_correct}/{total} = {total_correct/total*100:.0f}%" if total > 0 else "合计: 无数据")
for d in details:
    print(d)
