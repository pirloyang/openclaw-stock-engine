#!/usr/bin/env python3
"""
信号去重与状态管理 — 按信号类型冷却过滤

用法:
  python3 signal_dedup.py filter <引擎信号文件>
    → 输出过滤后的信号文件路径
    → 自动维护 /tmp/signal_state.json 状态

冷却规则:
  价格异动(price_action): 15分钟
  RSI超买/超卖(rsi): 60分钟
  量能(volume_surge/shrink): 15分钟
  均线金叉/死叉(ma_death_cross/ma_golden_cross): 每日1次
  MACD背离(macd): 每日1次
  突破/跌破(breakout/breakdown): 30分钟
  该涨不涨/逆势走强(should_rise/should_fall): 15分钟
  相对强度(relative_): 30分钟
  其他: 20分钟
"""

import sys, json, os, time
from datetime import datetime, timezone

STATE_FILE = "/root/.openclaw/workspace/data/signal_state.json"

# 信号类型→冷却分钟数映射（按规则名关键词匹配）
COOLING_RULES = {
    "daily": {"ma_death_cross", "ma_golden_cross", "macd_top_div", 
              "macd_bottom_div", "bearish_arrangement", "bullish_arrangement"},
    "hourly": {"rsi_overbought", "rsi_oversold"},
    "half_hour": {"relative_strength", "relative_weakness", "relative_strength_moderate",
                  "relative_weakness_moderate", "breakout_up", "breakdown", "2b_fake"},
    "quarter": {"price_action", "volume_surge", "volume_shrink", "vol_up_with_price",
                "vol_down_with_vol", "vol_down_shrink", "vol_up_no_vol",
                "should_rise_fail", "should_fall_strong"},
}

COOLING_MINUTES = {
    "daily": 1440,      # 每日1次
    "hourly": 60,       # 每60分钟1次
    "half_hour": 30,    # 每30分钟1次
    "quarter": 15,      # 每15分钟1次
}

def get_cooling_for_rule(rule_name):
    """determine cooling time based on rule name keywords"""
    for period, rules in COOLING_RULES.items():
        for pattern in rules:
            if pattern in rule_name:
                return COOLING_MINUTES[period]
    return 20  # 默认

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except:
            pass
    return {"signals": {}, "daily_date": datetime.now(timezone.utc).strftime("%Y-%m-%d")}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def filter_signals(signal_file):
    """Filter signals through dedup, return filtered file path"""
    if not os.path.exists(signal_file):
        print(f"信号文件不存在: {signal_file}")
        return None
    
    with open(signal_file) as f:
        data = json.load(f)
    
    state = load_state()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now_ts = time.time()
    
    # 每日重置：如果跨日了，清空状态
    if state.get("daily_date") != today:
        state = {"signals": {}, "daily_date": today}
    
    filtered = []
    suppressed_count = 0
    allowed_count = 0
    
    for item in data:
        code = item.get("code", "")
        sigs = item.get("signals", [])
        filtered_signals = []
        
        for sig in sigs:
            rule = sig.get("rule", "unknown")
            # 创建唯一key: code+rule
            key = f"{code}_{rule}"
            cooling_min = get_cooling_for_rule(rule)
            
            # 获取当前信号的值用于比较
            change = item.get("change_pct", "0")
            price = item.get("price", 0)
            sig_value = f"{price}_{change}"
            
            # 检查状态
            if key in state["signals"]:
                entry = state["signals"][key]
                elapsed = now_ts - entry["last_ts"]
                
                # 如果还在冷却期内，跳过
                if elapsed < cooling_min * 60:
                    suppressed_count += 1
                    entry["suppressed_count"] = entry.get("suppressed_count", 0) + 1
                    continue
                
                # 如果信号值相同（价格没变），缩短冷却期
                if entry.get("last_value") == sig_value:
                    if elapsed < cooling_min * 30:
                        suppressed_count += 1
                        entry["suppressed_count"] = entry.get("suppressed_count", 0) + 1
                        continue
            
            # 信号通过 → 更新状态
            state["signals"][key] = {
                "last_ts": now_ts,
                "last_triggered": datetime.now(timezone.utc).isoformat(),
                "last_value": sig_value,
                "cooling_minutes": cooling_min,
                "rule": rule,
                "code": code,
                "count": state["signals"].get(key, {}).get("count", 0) + 1,
                "suppressed_count": 0
            }
            filtered_signals.append(sig)
            allowed_count += 1
        
        # 如果过滤后有信号保留
        if filtered_signals:
            item["signals"] = filtered_signals
            filtered.append(item)
    
    # 清理24小时前的过期条目
    stale_threshold = now_ts - 86400
    state["signals"] = {k: v for k, v in state["signals"].items() 
                        if v.get("last_ts", 0) > stale_threshold}
    
    save_state(state)
    
    # 写出过滤后的信号文件
    filtered_file = signal_file.replace(".json", "_dedup.json")
    with open(filtered_file, "w") as f:
        json.dump(filtered, f, indent=2)
    
    print(f"信号去重: 通过{allowed_count}个, 抑制{suppressed_count}个", file=sys.stderr)
    
    return filtered_file

if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] != "filter":
        print(f"用法: {sys.argv[0]} filter <引擎信号文件>")
        sys.exit(1)
    
    result = filter_signals(sys.argv[2])
    if result:
        print(result)
