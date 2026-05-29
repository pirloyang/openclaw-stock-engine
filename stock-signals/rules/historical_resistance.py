#!/usr/bin/env python3
"""历史前高阻力检测"""
import json, sys

cache_path = sys.argv[1]
current_price = float(sys.argv[2])

rows = []
with open(cache_path) as f:
    for line in f:
        parts = line.strip().split()
        if len(parts) >= 2:
            try:
                rows.append({'close': float(parts[0]), 'vol': float(parts[1]),
                            'date': parts[2] if len(parts) > 2 else ''})
            except:
                pass

if len(rows) < 15:
    sys.exit(0)

closes = [r['close'] for r in rows]

# 局部高点：比前后各2天都高
peaks = []
for i in range(2, len(closes)-2):
    if closes[i] > closes[i-1] and closes[i] > closes[i-2] \
       and closes[i] > closes[i+1] and closes[i] > closes[i+2]:
        left_min = min(closes[max(0,i-5):i])
        right_min = min(closes[i+1:min(len(closes),i+6)])
        sig = (closes[i] / max(left_min, right_min, 0.01) - 1) * 100
        peaks.append({'price': closes[i], 'sig': round(sig, 1), 'date': rows[i]['date']})

# 放量阴线（可能的压力区）
avg_vol = sum(r['vol'] for r in rows[-20:]) / 20 if len(rows) >= 20 else sum(r['vol'] for r in rows) / len(rows)
for i in range(len(rows)-1, max(len(rows)-5, 0), -1):
    if i > 0 and rows[i]['close'] < rows[i-1]['close'] and avg_vol > 0:
        if rows[i]['vol'] > avg_vol * 1.5:
            peaks.append({'price': rows[i]['close'], 'sig': 10, 'date': rows[i]['date'], 'type': 'volume_spike'})

# 过滤+排序
peaks = [p for p in peaks if p['price'] > current_price * 0.7]
peaks.sort(key=lambda x: x['sig'], reverse=True)
peaks = peaks[:8]

# 检查当前价接近某个前高
for p in peaks:
    if p['price'] > current_price * 1.3:
        continue
    dist_pct = abs(current_price - p['price']) / p['price'] * 100
    if current_price <= p['price'] and dist_pct < 4 and p['price'] >= current_price * 0.85:
        # 股价在阻力位下方逼近 → 前高压制
        strength = 'very_high' if dist_pct < 1.5 else 'high'
        date_str = p['date'] if p['date'] else '历史'
        note = f'前高压制-{date_str}高点{p["price"]},距仅{dist_pct:.1f}%'
        result = {'rule': 'historical_resistance', 'direction': 'bearish',
                  'strength': strength, 'note': note}
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        sys.exit(0)
    elif current_price > p['price'] and dist_pct < 4 and p['price'] >= current_price * 0.85:
        # 股价已在阻力位上方（刚突破）→ 前高突破
        strength = 'high'
        date_str = p['date'] if p['date'] else '历史'
        note = f'前高突破-{date_str}高点{p["price"]},已超越{dist_pct:.1f}%'
        result = {'rule': 'historical_breakthrough', 'direction': 'bullish',
                  'strength': strength, 'note': note}
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        sys.exit(0)

# 模糊接近
for p in peaks[:3]:
    if p['price'] > current_price * 0.9 and p['price'] < current_price * 0.98:
        dist = (p['price'] - current_price) / current_price * 100
        if 0 < dist < 5:
            note = f'接近前高-{p.get("date","")}高点{p["price"]},距仅{dist:.1f}%'
            result = {'rule': 'approach_resistance', 'direction': 'bearish_warn',
                      'strength': 'medium', 'note': note}
            print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
            break
