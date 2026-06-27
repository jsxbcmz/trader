import json, re, urllib.request

url = "https://d.10jqka.com.cn/v6/time/hs_1A0001/defer/last.js"
with urllib.request.urlopen(url) as resp:
    raw = resp.read().decode('utf-8')

m = re.search(r'\((.*)\)', raw)
data = json.loads(m.group(1))['hs_1A0001']

pre = float(data['pre'])
minutes = []
for item in data['data'].split(';'):
    if not item.strip():
        continue
    parts = item.split(',')
    t = parts[0]
    price = float(parts[1])
    amount = float(parts[4])
    minutes.append((t, price, amount))

high = max(minutes, key=lambda x: x[1])
low = min(minutes, key=lambda x: x[1])
last = minutes[-1]
close_price = last[1]
total_amount = sum(m[2] for m in minutes)

print(f"昨收: {pre}")
print(f"开盘: {minutes[0][1]} ({((minutes[0][1]-pre)/pre*100):+.2f}%)")
print(f"最高: {high[1]} @ {high[0]}")
print(f"最低: {low[1]} @ {low[0]}")
print(f"收盘: {close_price} ({((close_price-pre)/pre*100):+.2f}%)")
print(f"成交额: {total_amount/1e12:.2f}万亿")

chg = (close_price - pre) / pre * 100
direction = "多头" if chg > 0.3 else ("空头" if chg < -0.3 else "震荡")
print(f"方向: {direction}")

open30 = [m for m in minutes if m[0] <= '1000']
morning = [m for m in minutes if '1000' < m[0] <= '1130']
afternoon = [m for m in minutes if m[0] > '1300']

if open30:
    oh = max(open30, key=lambda x: x[1])
    ol = min(open30, key=lambda x: x[1])
    print(f"\n开盘30min: 高{oh[1]} / 低{ol[1]}")
if morning:
    mh = max(morning, key=lambda x: x[1])
    ml = min(morning, key=lambda x: x[1])
    print(f"早盘: 高{mh[1]} / 低{ml[1]}")
if afternoon:
    ah = max(afternoon, key=lambda x: x[1])
    al = min(afternoon, key=lambda x: x[1])
    print(f"午盘: 高{ah[1]} / 低{al[1]}")
