import json, sys
from pathlib import Path
from collections import defaultdict
sys.path.insert(0, '/opt/data/workspace/trader')

# Load screening results
with open('/opt/data/output/screening_raw/2026-06-24.json') as f:
    raw = json.load(f)

results = raw['results']
market_avg = raw['market_avg']
is_bearish = raw['is_bearish_day']

# ============================================================
# 方案B：连涨回调因子 (OPT-W25-01)
# 前2个交易日市场均涨均为正且累计>1.5% → 全部置信度降一档
# ============================================================
raw_dir = Path('/opt/data/output/screening_raw')
all_raw_files = sorted(raw_dir.glob('*.json'))
current_idx = next((i for i, f in enumerate(all_raw_files) if raw['date'] in f.name), -1)

consecutive_up = False
consecutive_cum = 0.0
if current_idx >= 2:
    with open(all_raw_files[current_idx - 1]) as f:
        prev1 = json.load(f)
    with open(all_raw_files[current_idx - 2]) as f:
        prev2 = json.load(f)
    prev1_avg = prev1.get('market_avg', 0)
    prev2_avg = prev2.get('market_avg', 0)
    if prev1_avg > 0 and prev2_avg > 0:
        consecutive_cum = prev1_avg + prev2_avg
        if consecutive_cum > 1.5:
            consecutive_up = True

# Generate predictions
stocks = []
for r in results:
    symbol = r['symbol']
    name = r['name']
    industry = r['industry']
    score = r['score']
    grade = r['grade']
    group = r['group']
    day_change = r['day_change']
    close = r['close']
    is_limit_up = r.get('is_limit_up', False)
    limit_up_quality = r.get('limit_up_quality', '')
    q_score = r.get('q_score', 0)
    m_score = r.get('m_score', 0)
    s_score = r.get('s_score', 0)
    p_score = r.get('p_score', 0)
    r_penalty = r.get('r_penalty', 0)
    diff = r.get('diff', 0)
    dea = r.get('dea', 0)
    hist = r.get('hist', 0)
    brick_val = r.get('brick_val', 0)
    st_val = r.get('st_val', 0)
    ls_val = r.get('ls_val', 0)
    vol_ratio = r.get('vol_ratio', 1)
    turnover = r.get('turnover_rate', 0)
    cum_5d = r.get('cum_chg_5d', 0)
    summary = r.get('summary', '')

    # Determine direction
    macd_bullish = hist > 0
    macd_desc = "bullish" if macd_bullish else "bearish"

    if is_limit_up:
        if limit_up_quality == 'strong' and macd_bullish and score >= 65:
            direction = "pian_duo"
            confidence = "high"
        elif limit_up_quality == 'strong':
            direction = "pian_duo"
            confidence = "mid"
        else:
            direction = "pian_duo_cautious"
            confidence = "mid"
    elif group == 'strong_unsealed':
        if macd_bullish and score >= 60:
            direction = "pian_duo"
            confidence = "mid"
        elif macd_bullish:
            direction = "zhen_dang_pian_duo"
            confidence = "mid"
        else:
            direction = "zhong_xing_pian_kong"
            confidence = "low"
    else:
        if macd_bullish:
            direction = "zhong_xing_pian_duo"
            confidence = "low"
        else:
            direction = "zhong_xing_pian_kong"
            confidence = "low"

    # ============================================================
    # 方案C：过热标的强制方向限制 (OPT-W25-02 + OPT-W26-02)
    # 砖值>115 或 5日涨幅>13% 或 (砖值>100 且 5日>10%) → 方向不高于"震荡"
    # ============================================================
    is_overheated = (
        brick_val > 115
        or cum_5d > 13
        or (brick_val > 100 and cum_5d > 10)
    )
    if is_overheated:
        # 强制方向不高于 zhen_dang（震荡）
        if direction in ("pian_duo", "pian_duo_cautious", "zhen_dang_pian_duo", "zhong_xing_pian_duo"):
            direction = "zhen_dang"
            confidence = "low"

    # ============================================================
    # 方案A：普跌日置信度强制降档 (OPT-W24-05)
    # 普跌日(market_avg < -1%)时：偏多→中性偏多，置信度上限"中低"
    # ============================================================
    if is_bearish:
        # 方向降档
        dir_downgrade = {
            "pian_duo": "zhong_xing_pian_duo",
            "pian_duo_cautious": "zhong_xing_pian_duo",
            "zhen_dang_pian_duo": "zhen_dang",
        }
        if direction in dir_downgrade:
            direction = dir_downgrade[direction]
        # 置信度上限为 mid-low
        conf_cap = {"high": "mid", "mid": "low"}
        if confidence in conf_cap:
            confidence = conf_cap[confidence]

    # ============================================================
    # 方案D：涨停质量×环境交叉修正 (OPT-W24-02 修正版)
    # neutral封板 + 普跌日 → 置信度额外降一档
    # ============================================================
    if is_limit_up and limit_up_quality == 'neutral' and is_bearish:
        conf_down_d = {"mid": "low", "low": "low"}
        if confidence in conf_down_d:
            confidence = conf_down_d[confidence]

    # ============================================================
    # 方案B：连涨回调因子 (OPT-W25-01)
    # 前2日连涨且累计>1.5% → 置信度降一档
    # ============================================================
    if consecutive_up:
        conf_down_b = {"high": "mid", "mid": "low"}
        if confidence in conf_down_b:
            confidence = conf_down_b[confidence]

    # Risk factors
    risks = []
    if not macd_bullish:
        risks.append("MACD bearish")
    if vol_ratio > 3:
        risks.append("abnormal volume(vol_ratio={:.1f})".format(vol_ratio))
    if cum_5d > 13:
        risks.append("5d gain overheated({:+.1f}%)".format(cum_5d))
    if brick_val > 115:
        risks.append("brick_val overheated({:.0f})".format(brick_val))
    if turnover > 20:
        risks.append("high turnover({:.1f}%)".format(turnover))
    if r_penalty < 0:
        risks.append("risk penalty({})".format(r_penalty))
    if is_bearish:
        risks.append("bear market day(avg{:+.2f}%)".format(market_avg))
    if is_overheated:
        risks.append("⚠️OVERHEATED: direction capped at zhen_dang")
    if consecutive_up:
        risks.append("连涨回调风险(前2日累计{:+.1f}%)".format(consecutive_cum))
    if is_limit_up and limit_up_quality == 'neutral' and is_bearish:
        risks.append("neutral封板+普跌日=延续性弱")

    risk_text = "; ".join(risks) if risks else "no major risk"

    # Support/Resistance
    support = close * 0.97 if day_change > 0 else close * 0.95
    resistance = close * 1.03 if day_change > 0 else close * 1.05

    # Build detailed_analysis
    vol_desc = "fang_liang" if vol_ratio > 1.5 else ("suo_liang" if vol_ratio < 0.7 else "normal")

    analysis = (
        f"**{name}({symbol})** | {industry} | {grade}/{score:.1f} | {direction}\n\n"
        f"Day: {group} | chg {day_change:+.2f}% | close {close} | {vol_desc}(ratio {vol_ratio:.1f})\n"
        f"5d cum: {cum_5d:+.2f}% | turnover: {turnover:.1f}%\n"
        f"Pattern: {summary}\n"
        f"Quality: Q={q_score} M={m_score} S={s_score} P={p_score} | raw={r['raw_total']:.0f}\n\n"
        f"MACD: DIFF={diff:.2f} DEA={dea:.2f} HIST={hist:.2f} {macd_desc}\n"
        f"Brick={brick_val:.1f} | ST={st_val:.1f} | LS={ls_val:.1f}\n\n"
        f"Shape: {summary}. MACD {'bull' if macd_bullish else 'bear'}."
    )

    if is_limit_up:
        analysis += f" Limit-up, quality='{limit_up_quality}'."
    else:
        analysis += f" Unsealed +{day_change:.1f}%, {vol_desc}."

    analysis += (
        f"\nSupport: {support:.2f} | Resistance: {resistance:.2f}\n"
        f"Risk: {risk_text}\n"
        f"Prediction: {direction}, confidence={confidence}."
    )

    stock = {
        'symbol': symbol,
        'name': name,
        'industry': industry,
        'score': score,
        'grade': grade,
        'group': group,
        'day_change': day_change,
        'close': close,
        'direction': direction,
        'confidence': confidence,
        'source': 'auto',
        'detailed_analysis': analysis,
        'cum_chg_5d': cum_5d,
        'vol_ratio': vol_ratio,
        'turnover_rate': turnover,
        'limit_up_quality': limit_up_quality,
        'q_score': q_score,
        'm_score': m_score,
        's_score': s_score,
        'p_score': p_score,
        'r_penalty': r_penalty,
        'raw_total': r['raw_total'],
        'diff': diff,
        'dea': dea,
        'hist': hist,
        'brick_val': brick_val,
        'st_val': st_val,
        'ls_val': ls_val,
        'risk_factors': risks,
        'support': round(support, 2),
        'resistance': round(resistance, 2),
        'macd_bullish': macd_bullish,
        'is_limit_up': is_limit_up,
    }
    stocks.append(stock)

# ============================================================
# 方案E：板块过热预警 (OPT-W26-03)
# 同板块>=3只入选 且 板块均值5日涨幅>8% → 置信度降一档
# ============================================================
sector_stocks = defaultdict(list)
for s in stocks:
    sector_stocks[s['industry']].append(s)

for industry, group in sector_stocks.items():
    if len(group) >= 3:
        avg_5d = sum(s['cum_chg_5d'] for s in group) / len(group)
        if avg_5d > 8:
            for s in group:
                conf_down_e = {"high": "mid", "mid": "low"}
                if s['confidence'] in conf_down_e:
                    s['confidence'] = conf_down_e[s['confidence']]
                s['risk_factors'].append(
                    f"板块过热({industry}:{len(group)}只,均涨{avg_5d:.1f}%)"
                )

output = {
    'date': '2026-06-24',
    'market_avg': market_avg,
    'is_bearish_day': is_bearish,
    'total': len(stocks),
    'stocks': stocks
}

with open('/opt/data/output/screening_predictions/2026-06-24.json', 'w') as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

# Print summary
for s in stocks:
    macd_str = "MACD-bull" if s['macd_bullish'] else "MACD-bear"
    print(f"{s['symbol']} {s['name']:6s} {s['score']:5.1f}{s['grade']} {s['group']:20s} -> {s['direction']:20s} conf={s['confidence']} {macd_str}")

print(f"\nTotal: {len(stocks)} stocks")
print("Written to: /opt/data/output/screening_predictions/2026-06-24.json")
