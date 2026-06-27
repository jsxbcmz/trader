import json, sys
sys.path.insert(0, '/opt/data/workspace/trader')

# Load screening results
with open('/opt/data/output/screening_raw/2026-06-24.json') as f:
    raw = json.load(f)

results = raw['results']
market_avg = raw['market_avg']
is_bearish = raw['is_bearish_day']

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

    # Risk factors
    risks = []
    if not macd_bullish:
        risks.append("MACD bearish")
    if vol_ratio > 3:
        risks.append("abnormal volume(vol_ratio={:.1f})".format(vol_ratio))
    if cum_5d > 15:
        risks.append("5d gain too high({:+.1f}%)".format(cum_5d))
    if turnover > 20:
        risks.append("high turnover({:.1f}%)".format(turnover))
    if r_penalty < 0:
        risks.append("risk penalty({})".format(r_penalty))
    if is_bearish:
        risks.append("bear market day")

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
