#!/usr/bin/env python3
"""筹码分布分析 — 从日线OHLCV数据计算成本密集区、获利盘比例、套牢盘压力"""
import json, sys

cache_path = sys.argv[1] if len(sys.argv) > 1 else ""
current_price = float(sys.argv[2]) if len(sys.argv) > 2 else 0

if not cache_path or not current_price:
    sys.exit(0)

# 读缓存（格式: close vol open high low date）
rows = []
with open(cache_path) as f:
    for line in f:
        parts = line.strip().split()
        if len(parts) >= 6:
            try:
                rows.append({
                    'close': float(parts[0]),
                    'vol': float(parts[1]),
                    'open': float(parts[2]),
                    'high': float(parts[3]),
                    'low': float(parts[4]),
                    'date': parts[5]
                })
            except:
                pass
        elif len(parts) >= 3:
            try:
                rows.append({
                    'close': float(parts[0]),
                    'vol': float(parts[1]),
                    'date': parts[2]
                })
            except:
                pass

if len(rows) < 15:
    sys.exit(0)

# 1. 计算30日VWAP（成交量加权平均价）
recent_30 = rows[-30:] if len(rows) >= 30 else rows
total_vol_vwap = sum(r['vol'] for r in recent_30 if r['vol'] > 0)
vwap_30 = sum(r['close'] * r['vol'] for r in recent_30 if r['vol'] > 0) / total_vol_vwap if total_vol_vwap > 0 else current_price

# 2. 计算60日价格区间百分位
all_prices = [r['close'] for r in rows]
price_low = min(all_prices)
price_high = max(all_prices)
price_range = price_high - price_low
position_pct = ((current_price - price_low) / price_range * 100) if price_range > 0 else 50

# 识别位置状态
if position_pct >= 85:
    position_status = "高位"
    position_score = -1  # 高位抑制
elif position_pct >= 65:
    position_status = "中高位"
    position_score = -0.5
elif position_pct >= 35:
    position_status = "中位"
    position_score = 0
elif position_pct >= 15:
    position_status = "中低位"
    position_score = 0.5
else:
    position_status = "低位"
    position_score = 1  # 低位支撑

# 3. 套牢盘压力：找前10日内放量阴线（潜在抛压区）
avg_vol = sum(r['vol'] for r in recent_30) / len(recent_30) if len(recent_30) > 0 else 1
resistance_zones = []
for r in rows[-15:]:
    close, open_px = r['close'], r.get('open', 0)
    if open_px and close < open_px and r['vol'] > avg_vol * 1.5:
        resistance_zones.append({
            'price': close,
            'vol_ratio': r['vol'] / avg_vol if avg_vol > 0 else 1,
            'date': r['date']
        })

# 是否当前价在套牢盘区域
near_resistance = False
for z in resistance_zones:
    if abs(current_price - z['price']) / z['price'] < 0.03:
        near_resistance = True
        break

# 4. 成本偏离度：当前价 vs 30日VWAP（主力成本区）
deviation_from_cost = (current_price / vwap_30 - 1) * 100

# 成本偏离评分
if abs(deviation_from_cost) < 3:
    cost_status = "成本附近"
    cost_score = 0  # 中性
elif deviation_from_cost > 10:
    cost_status = "大幅偏离成本"
    cost_score = -0.5  # 获利盘丰厚，兑现压力
elif deviation_from_cost > 5:
    cost_status = "轻度偏离成本"
    cost_score = -0.2
elif deviation_from_cost < -5:
    cost_status = "低于成本"
    cost_score = 0.5  # 超跌，资金可能自救

# 输出规则信号
signals = []

# 信号1：套牢盘压力（前高附近有放量阴线）
if near_resistance and position_pct >= 65:
    signals.append({
        "rule": "chip_resistance",
        "direction": "sell_signal",
        "strength": "high",
        "note": f"筹码压力-前{len(resistance_zones)}日有放量阴线套牢区,当前在压力位附近"
    })

# 信号2：低位筹码密集（价格在30日VWAP附近且处于中低位）
if abs(deviation_from_cost) < 5 and position_pct < 45:
    signals.append({
        "rule": "chip_density_low",
        "direction": "buy_signal",
        "strength": "medium",
        "note": f"低位筹码密集-距主力成本仅{abs(deviation_from_cost):.1f}%,处于{position_status}({position_pct:.0f}%)"
    })

# 信号3：偏离成本过大（获利盘丰厚，需警惕兑现）
if deviation_from_cost > 15:
    signals.append({
        "rule": "chip_deviation_high",
        "direction": "bearish_warn",
        "strength": "medium",
        "note": f"大幅偏离成本{deviation_from_cost:.0f}%—获利盘丰厚,追高风险大"
    })

# 信号4：低于成本区（可能被低估，但也可能是趋势下行）
if deviation_from_cost < -8:
    signals.append({
        "rule": "chip_below_cost",
        "direction": "overshoot",
        "strength": "medium",
        "note": f"低于主力成本{abs(deviation_from_cost):.0f}%—超跌区域"
    })

for sig in signals:
    print(json.dumps(sig, ensure_ascii=False, separators=(",", ":")))
