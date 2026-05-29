#!/usr/bin/env python3
"""盘中定点监控：每5分钟扫描关键止损/清仓线 + 大盘指数趋势"""
import urllib.request
import json
import time
import os
import sys
from datetime import datetime

# 大盘指数监控
INDICES = {
    "sh000001": ("上证指数", None),
    "sz399001": ("深证成指", None),
    "sz399006": ("创业板指", None),
    "sh000688": ("科创50", None),
}

# 持仓止损线
STOP_WATCH = {
    "sh600584": ("长电科技", 54.10, "止盈线"),
    "sz002050": ("三花智控", 46.87, "止盈线"),
    "sh600487": ("亨通光电", 67.85, "止损线"),
    "sz300115": ("长盈精密", 35.69, "止损线"),
    "sz000969": ("安泰科技", 22.40, "止损线"),
    "sz002463": ("沪电股份", 104.07, "止盈线"),
}

# ========== 今日英伟达链第一梯队监控 ==========
# 澜起科技 688008 — 核心首选
# 天孚通信 300394 — 超跌反弹
# 光迅科技 002281 — 强势延续
# 工业富联 601138 — 趋势反转
FIRST_TIER_WATCH = {
    "sh688008": {
        "name": "澜起科技",
        "entry_low": 253.0,
        "entry_high": 258.0,
        "good_entry": True,
        "stop": 247.0,
        "note": "首选！回踩253-255低吸，竞价开在262以内可介入1/3"
    },
    "sz300394": {
        "name": "天孚通信",
        "entry_low": 355.0,
        "entry_high": 360.0,
        "good_entry": True,
        "stop": 351.0,
        "note": "超跌反弹，回踩355不破试仓"
    },
    "sz002281": {
        "name": "光迅科技",
        "entry_low": 225.0,
        "entry_high": 228.0,
        "good_entry": True,
        "stop": 220.0,
        "note": "强势低吸，回踩MA5(225附近)参与，追高放弃"
    },
    "sh601138": {
        "name": "工业富联",
        "entry_low": 68.5,
        "entry_high": 69.0,
        "good_entry": True,
        "stop": 67.0,
        "note": "放量站上68.5右侧追入"
    },
}

# 大盘环境判断阈值
MARKET_FILTER = {
    "open_reversal": 0.5,     # 开盘冲高回落>0.5%触发过滤器
    "downtrend": 1.0,         # 单边下跌>1%触发暂停买入
}

def fetch_all():
    """一次拉取所有标的（指数+持仓止损）"""
    all_codes = list(INDICES.keys()) + list(STOP_WATCH.keys())
    url = f"http://hq.sinajs.cn/list={','.join(all_codes)}"
    req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        raw = resp.read().decode('gbk')
    
    indices = {}
    stops = {}
    
    for line in raw.strip().split('\n'):
        if '=' not in line: continue
        parts = line.split(',')
        # Extract code
        try:
            code_part = line.split('_')[-1].split('=')[0]
        except:
            continue
        name_raw = parts[0].split('=')[1].strip('"') if '="' in parts[0] else "?"
        try:
            open_p = float(parts[1])
            prev_close = float(parts[2])
            cur = float(parts[3])
            high = float(parts[4])
            low = float(parts[5])
            change = (cur - prev_close) / prev_close * 100
            amp = (high - low) / prev_close * 100
            item = {"name": name_raw, "price": cur, "change": change, "open": open_p, "high": high, "low": low, "amp": amp}
            
            if code_part in INDICES:
                indices[code_part] = item
            if code_part in STOP_WATCH:
                stops[code_part] = item
        except:
            continue
    
    return indices, stops

def check_market_environment(indices):
    """市场环境过滤器"""
    alerts = []
    
    # 上证趋势
    if "sh000001" in indices:
        sh = indices["sh000001"]
        sh_open = sh["open"]
        sh_cur = sh["price"]
        sh_high = sh["high"]
        sh_change = sh["change"]
        
        # 开盘冲高回落检测
        if sh_high > sh_open:  # 高开
            pullback = (sh_high - sh_cur) / sh_high * 100
            if pullback > MARKET_FILTER["open_reversal"] and sh_change < 0:
                alerts.append(f"🔴 市场过滤器激活 | 上证高开低走回落{pullback:.1f}%至{sh_cur:.0f}({sh_change:+.2f}%)→暂停所有买入操作")
        
        # 单边下跌检测
        if sh_change < -MARKET_FILTER["downtrend"] and sh_cur <= sh_open:
            alerts.append(f"🟡 大盘预警 | 上证单边下跌{sh_change:+.2f}%→买入信号降级为观察")
    
    # 深成指
    if "sz399001" in indices:
        sz = indices["sz399001"]
        if sz["change"] < -2:
            alerts.append(f"🟡 深成指{sz['change']:+.2f}%→中小盘普跌，个股操作风险增大")
    
    return alerts

def check_stop_losses(stops):
    """止损线扫描"""
    alerts = []
    for code, (name, stop_price, label) in STOP_WATCH.items():
        if code in stops:
            p = stops[code]
            margin = p["price"] - stop_price
            margin_pct = margin / stop_price * 100 if stop_price else 0
            
            if p["price"] <= stop_price:
                alerts.append(f"❌ {name} | 现价{p['price']} 触发{label}({stop_price}) | 距阈值{margin:.2f} | 当日{p['change']:+.2f}%")
            elif margin_pct < 2:
                alerts.append(f"⚠️ {name} | 现价{p['price']} 接近{label}({stop_price}) | 仅剩{margin_pct:.1f}%空间 | 当日{p['change']:+.2f}%")
    return alerts

def check_first_tier_today(all_stocks):
    """第一梯队介入机会扫描"""
    alerts = []
    for code, info in FIRST_TIER_WATCH.items():
        if code in all_stocks:
            p = all_stocks[code]
            price = p["price"]
            change = p["change"]
            entry_low = info["entry_low"]
            entry_high = info["entry_high"]
            stop = info["stop"]
            
            # 在介入区间内
            if entry_low <= price <= entry_high:
                margin_to_stop = (price - stop) / stop * 100
                if margin_to_stop > 2:  # 距离止损至少2%
                    alerts.append(f"🔵 机会 | {info['name']} | 现价{price}({change:+.2f}%) 进入介入区间[{entry_low}-{entry_high}] | 止损{stop} | 空间{margin_to_stop:.1f}% | {info['note']}")
                else:
                    alerts.append(f"🟡 接近 | {info['name']} | 现价{price} 在介入区间但距止损过近({margin_to_stop:.1f}%) | {info['note']}")
            
            # 价格突破介入区间上限（强势拉升但未失控）
            elif price > entry_high and price < entry_high * 1.03:
                if change > 2:
                    alerts.append(f"🟢 强势 | {info['name']} | 现价{price}({change:+.2f}%) 突破介入区间上限，放量确认可追 | 止损{stop}")
    return alerts

def fetch_first_tier():
    """拉取第一梯队+NVIDIA链行情"""
    first_codes = list(FIRST_TIER_WATCH.keys())
    url = f"http://hq.sinajs.cn/list={','.join(first_codes)}"
    req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        raw = resp.read().decode('gbk')
    
    stocks = {}
    for line in raw.strip().split('\n'):
        if '=' not in line: continue
        try:
            code_part = line.split('_')[-1].split('=')[0]
        except:
            continue
        parts = line.split(',')
        try:
            cur = float(parts[3])
            prev_close = float(parts[2])
            open_p = float(parts[1])
            high = float(parts[4])
            low = float(parts[5])
            change = (cur - prev_close) / prev_close * 100
            amp = (high - low) / prev_close * 100
            stocks[code_part] = {"price": cur, "change": change, "open": open_p, "high": high, "low": low, "amp": amp}
        except:
            continue
    return stocks

def main():
    now = datetime.now()
    # 交易时间检查
    if now.weekday() >= 5:
        return []
    t = now.hour * 60 + now.minute
    if not ((9*60+30 <= t <= 11*60+30) or (13*60 <= t <= 15*60)):
        return []
    
    indices, stops = fetch_all()
    first_tier_stocks = fetch_first_tier()
    
    alerts = []
    market_alerts = check_market_environment(indices)
    stop_alerts = check_stop_losses(stops)
    first_tier_alerts = check_first_tier_today(first_tier_stocks)
    
    alerts.extend(market_alerts)
    alerts.extend(stop_alerts)
    alerts.extend(first_tier_alerts)
    
    return alerts

if __name__ == "__main__":
    alerts = main()
    if alerts:
        msg = "\n".join(alerts)
        print(f"ALERT|{msg}")
        with open("/tmp/spot_alert.txt", "w") as f:
            f.write(msg + "\n")
            f.write(f"---\n{datetime.now().strftime('%H:%M:%S')}\n")
    else:
        # Status output
        indices, stops = fetch_all()
        if indices:
            for code, item in sorted(indices.items()):
                print(f"IDX|{item['name']}|{item['price']}|{item['change']:+.2f}%|振幅{item['amp']:.1f}%")
        if stops:
            for code, (name, stop, label) in STOP_WATCH.items():
                if code in stops:
                    p = stops[code]
                    m = p["price"] - stop
                    print(f"STOP|{name}|现价{p['price']}|距阈值{m:+.2f}|当日{p['change']:+.2f}%")
