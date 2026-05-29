#!/usr/bin/env python3
"""全面盯盘监控 - 持仓 + 算力板块
交易日每30分钟检查，发现异动/机会时发送微信通知
"""
import urllib.request
import re
from datetime import datetime

# ==== 配置区 ====

# 扩展的算力板块关注标的
WATCH_LIST = {
    "sz300394": ("天孚通信", "重点", 0),
    "sz300308": ("中际旭创", "参考", 0),
    "sz300502": ("新易盛", "参考", 0),
    "sh688041": ("海光信息", "参考", 0),
    "sz000938": ("紫光股份", "重点", 0),
    "sh603019": ("中科曙光", "重点", 0),
    "sz300474": ("景嘉微", "重点", 0),
}

# 动态读取 TOOLS.md 中的持仓（不再硬编码）
import subprocess, json
def _load_holdings_from_tools():
    """调用 tools.sh holdings 动态获取当前真实持仓"""
    try:
        result = subprocess.run(
            ["bash", "/root/.openclaw/workspace/scripts/tools.sh", "holdings"],
            capture_output=True, text=True, timeout=10
        )
        holdings = {}
        for line in result.stdout.strip().split("\n"):
            parts = line.split()
            if len(parts) >= 4:
                code, name, shares, cost = parts[0], parts[1], int(parts[2]), float(parts[3])
                prefix = "sh" if code.startswith("6") else "sz"
                holdings[f"{prefix}{code}"] = (name, shares, cost)
        return holdings
    except Exception:
        return {}
HOLDINGS = _load_holdings_from_tools()

def fetch_all():
    codes = ",".join(list(WATCH_LIST.keys()) + list(HOLDINGS.keys()))
    url = f"https://qt.gtimg.cn/q={codes}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    resp = urllib.request.urlopen(req, timeout=10)
    data = resp.read()
    # Try to decode, handle encoding issues
    return data.decode("gbk", errors="replace")

def parse_quotes(data, code_list):
    """Parse quotes from Tencent data"""
    results = {}
    for code in code_list:
        # Find the matching v_code="..." line
        pattern = r'v_' + re.escape(code) + r'="([^"]*)"'
        m = re.search(pattern, data)
        if not m:
            continue
        fields = m.group(1).split("~")
        
        # Standard format: 0=exchange, 1=name, 2=code, 3=price, 4=yclose
        # But garbled names can mess up positions. Find the code field.
        # The code (e.g., "600487") should be at a known position.
        # Actually, the format is reliable: name is at idx 1, code at idx 2
        # But with gbk encoding, the name might have split tildes
        
        # Try to find the numeric code in the fields
        code_idx = None
        for i, f in enumerate(fields):
            if f == code.replace("sh", "").replace("sz", ""):
                code_idx = i
                break
        
        if code_idx is None or code_idx + 3 >= len(fields):
            continue
        
        # Once we have code at code_idx, price is code_idx+1, yclose is code_idx+2
        price = float(fields[code_idx + 1]) if fields[code_idx + 1] else 0
        yclose = float(fields[code_idx + 2]) if len(fields) > code_idx + 2 and fields[code_idx + 2] else 0
        
        # Change% is at a fixed offset from the end-ish. Let me try different approaches.
        # The change field in standard format is 32, change% is 33
        # But with shifted positions, let me look for it differently
        
        # Better approach: compute from price and yclose
        if yclose > 0:
            change_amt = price - yclose
            change_pct = (price / yclose - 1) * 100
        else:
            change_amt = 0
            change_pct = 0
            
        results[code] = (price, yclose, change_amt, change_pct)
    
    return results

def main():
    now = datetime.now()
    hour = now.hour
    minute = now.minute
    time_str = now.strftime("%H:%M")
    
    # Skip check before 9:25 (call auction starts final pricing)
    # and after 15:00
    current_time = hour * 60 + minute
    if current_time < 9 * 60 + 25 or current_time >= 15 * 60 + 30:
        print(f"当前 {time_str}，非交易时段，跳过")
        return
    
    is_auction = 9 * 60 + 25 <= current_time <= 9 * 60 + 30
    
    data = fetch_all()
    
    all_codes = list(WATCH_LIST.keys()) + list(HOLDINGS.keys())
    quotes = parse_quotes(data, all_codes)
    
    if is_auction:
        print(f"⚡ {time_str} [集合竞价阶段]")
    else:
        print(f"📊 {time_str} [盘中扫描]")
    print()
    
    # ---- 持仓监控 ----
    print("=== 持仓现状 ===")
    total_market = 0
    alerts = []
    
    for code, (name, shares, cost) in HOLDINGS.items():
        if code not in quotes:
            print(f"{name}: 暂无数据")
            continue
        price, yclose, chg_amt, chg_pct = quotes[code]
        if price == 0:
            print(f"{name}: 停牌或未开盘")
            continue
        
        market_val = price * shares
        cost_val = cost * shares
        pnl = market_val - cost_val
        total_market += market_val
        
        # Color/icon
        icon = "🔴" if chg_pct > 0 else "🟢" if chg_pct < 0 else "⚪"
        pnl_icon = "✅" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
        
        print(f"  {icon} {name}: ¥{price:.2f} ({chg_pct:+.2f}%)")
        print(f"    持仓{shares}股 | 市值¥{market_val:.0f} | {pnl_icon} ¥{pnl:+.0f}")
        
        # Generate alerts for significant moves
        abs_pct = abs(chg_pct)
        if abs_pct >= 3 and not is_auction:
            direction = "大涨" if chg_pct > 0 else "大跌"
            alerts.append(f"⚠️ {name} 今日{direction} {chg_pct:+.2f}%!")
        if abs_pct >= 5 and not is_auction:
            direction = "暴涨" if chg_pct > 0 else "暴跌"
            alerts.append(f"🔴 {name} 今日{direction} {chg_pct:+.2f}%!")
    
    print(f"  总持仓市值: ¥{total_market:,.0f}")
    
    # 动态止损检查：任一持仓浮亏超过-8%时告警
    for code, (name, shares, cost) in HOLDINGS.items():
        if code in quotes and quotes[code][0] > 0:
            price = quotes[code][0]
            pnl_pct = (price / cost - 1) * 100
            if pnl_pct < -8:
                alerts.append(f"🔴 {name} 浮亏{pnl_pct:.1f}%，注意止损信号")
    
    # ---- 算力板块机会监控 ----
    print()
    print("=== 算力板块机会 ===")
    
    opportunities = []
    for code, (name, tier, _) in WATCH_LIST.items():
        if code not in quotes:
            continue
        price, yclose, chg_amt, chg_pct = quotes[code]
        if price == 0:
            continue
        
        icon = "🔴" if chg_pct > 0 else "🟢" if chg_pct < 0 else "⚪"
        print(f"  {icon} {name}: ¥{price:.2f} ({chg_pct:+.2f}%) [{tier}]")
        
        # Entry opportunity signals
        if 0.5 <= chg_pct <= 3.0 and tier == "重点":
            opportunities.append(f"⭐ 机会: {name} 涨{chg_pct:+.2f}%，仍有介入空间!")
    
    print()
    
    # ---- 输出可操作信号 ----
    all_signals = alerts + opportunities
    
    if all_signals:
        for s in all_signals:
            print(s)
    else:
        print("💤 当前无特殊异动，一切正常")

if __name__ == "__main__":
    main()
