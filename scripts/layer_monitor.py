#!/usr/bin/env python3
"""
分层监控采集+信号判断引擎
- 一次批量curl全量数据（150+只，<3秒）
- 分层计算阈值
- 写入分层signal文件供推送层读取
- 无推送，永不超时
"""
import os, json, subprocess, time, re, sys
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path
from l6_hot_alerts import compute_l6_hot

WORKSPACE = "/root/.openclaw/workspace"
sys.path.insert(0, WORKSPACE + "/scripts")
ALERT_DIR = "/tmp/stock_alerts"
os.makedirs(ALERT_DIR, exist_ok=True)

# ========== 定义全量代码 ==========

# L1: 大盘指数
INDICES = {
    "sh000001": ("上证指数", "指数"),
    "sz399001": ("深证成指", "指数"),
    "sz399006": ("创业板指", "指数"),
    "sh000688": ("科创50", "指数"),
}

# L2: 当前持仓（从TOOLS.md动态读）
# L3: 重点监控标的
# L4: ETF
ETF = {
    "sh516640": ("芯片ETF富国", "ETF"),
    "sz159667": ("工业母机ETF", "ETF"),
    "sz159858": ("创新药ETF", "ETF"),
    "sz159928": ("消费ETF", "ETF"),
    "sh512400": ("有色金属ETF", "ETF"),
}

# L5: 自选池（从tools.sh holdings/history读）

def get_current_holdings():
    """从TOOLS.md解析当前持仓"""
    path = f"{WORKSPACE}/TOOLS.md"
    if not os.path.exists(path):
        return {}
    holdings = {}
    in_section = False
    with open(path) as f:
        for line in f:
            if line.strip().startswith("### 持仓"):
                in_section = True
                continue
            if in_section:
                if line.strip().startswith("###"):
                    break
                m = re.search(r'-\s*(.+?)\s+(\d{6})\s*[（(](\d+)股.*?成本([\d.]+)', line)
                if m:
                    # 跳过已清仓的（含'清仓'标识的行，但排除"误报清仓"等否定表述）
                    if '清仓' in line and '误报' not in line:
                        continue
                    name = m.group(1).strip()
                    code = m.group(2)
                    shares = int(m.group(3))
                    cost = float(m.group(4))
                    holdings[code] = {"name": name, "shares": shares, "cost": cost}
    return holdings

def get_watchlist_codes():
    """从tools.sh获取所有自选代码"""
    result = subprocess.run(
        ["bash", f"{WORKSPACE}/scripts/tools.sh", "holdings"],
        capture_output=True, text=True, timeout=30
    )
    codes = []
    for line in result.stdout.strip().split('\n'):
        parts = line.strip().split()
        if parts and parts[0].isdigit():
            codes.append(parts[0])
    
    # 额外自选
    extra = ["300456","002281","300620","601138","000977","300476","000034",
             "002837","300499","301018","300738","300383","001309","300475",
             "002119","300302","300661","688798","300223","603881","300857",
             "000032","002335","600602","600118","002025","300045","688568",
             "300762","600343","300455","688523","301306","002465","600391",
             "600592","301005","000901","002682","600151","000551","300265",
             "002361","003009","600345","002151","688008","300394","300502",
             "600522","300750","002230","002384","000988","000636","300660",
             "002938","002881","300503","301182","300964","603618","603893",
             "000938","002195","301308","601600","000592","600409","600549",
             "000547","002185","600100","300017","600105","600879","300102",
             "002553","603256","600183","002916","300058","300113","300442",
             "002463","600584","600487","002050","300115"]
    for c in extra:
        if c not in codes:
            codes.append(c)
    return sorted(set(codes))

# ========== 数据采集 ==========

def fetch_batch_prices():
    """一次性批量curl全量数据"""
    holdings = get_current_holdings()
    watchlist_codes = get_watchlist_codes()
    
    # 构建完整查询字符串
    all_codes = []
    query_parts = []
    
    # 指数（INDICES的key已经带sh/sz前缀，直接使用）
    for tag in INDICES:
        query_parts.append(tag)
    
    # ETF
    for tag in ETF:
        query_parts.append(tag)
    
    # 持仓
    for code in holdings:
        prefix = "sh" if code.startswith("6") else "sz"
        query_parts.append(f"{prefix}{code}")
    
    # 重点 = 自选中的核心标的
    focus_codes = ["000969","300660","002938","002881","000988","000636"]
    for code in focus_codes:
        pfx = "sh" if code.startswith(("6","9")) else "sz"
        q = f"{pfx}{code}"
        if q not in query_parts:
            query_parts.append(q)
    
    # 自选
    for code in watchlist_codes:
        if len(code) != 6: continue
        prefix = "sh" if code.startswith(("6","9")) else "sz"
        q = f"{prefix}{code}"
        if q not in query_parts:
            query_parts.append(q)
    
    # 去除可能重复
    query_parts = list(dict.fromkeys(query_parts))  # 保持顺序去重
    query_str = ",".join(query_parts)
    
    # 单次批量curl
    t0 = time.time()
    result = subprocess.run(
        ["curl", "-s", "--max-time", "15", f"https://qt.gtimg.cn/q={query_str}"],
        capture_output=True, timeout=20
    )
    raw = result.stdout.decode('GBK', errors='replace')
    t1 = time.time()
    
    fetch_time = round(t1 - t0, 2)
    
    # 解析每条数据
    stocks = {}
    for line in raw.split('";\n'):
        line = line.strip().strip('";').strip()
        if not line or not line.startswith('v_'):
            continue
        parts = line.split('~')
        if len(parts) < 40:
            continue
        try:
            code = parts[2]  # 第三字段是股票代码
            name = parts[1]
            price = float(parts[3]) if parts[3] else 0
            prev_close = float(parts[4]) if parts[4] else 0
            open_p = float(parts[5]) if parts[5] else 0
            volume = int(parts[6]) if parts[6] else 0  # 手
            change_pct = parts[32] if len(parts) > 32 else "0"
            change_pct = float(change_pct.strip('%'))
            high = float(parts[33]) if len(parts) > 33 and parts[33] else 0
            low = float(parts[34]) if len(parts) > 34 and parts[34] else 0
            turnover = float(parts[38]) if len(parts) > 38 and parts[38] else 0
            amount = float(parts[37]) if len(parts) > 37 and parts[37] else 0
            
            stocks[code] = {
                "code": code, "name": name, "price": price,
                "prev_close": prev_close, "open": open_p,
                "change_pct": change_pct, "high": high, "low": low,
                "volume": volume, "turnover": turnover, "amount": amount
            }
        except (ValueError, IndexError):
            continue
    
    return stocks, fetch_time, holdings, watchlist_codes, focus_codes

# ========== 分层信号判断 ==========

def compute_l1_market(stocks):
    """L1: 大盘指数分析"""
    signals = []
    for code, info in INDICES.items():
        s = stocks.get(code[2:])
        if not s: continue
        chg = s['change_pct']
        # 判断
        if chg >= 1:
            level = "🔴强势" if chg >= 2 else "🟡偏强"
            trend = "↑" if chg > 0 else "↓"
        elif chg <= -1:
            level = "🟢弱势" if chg <= -2 else "🔵偏弱"
            trend = "↓" if chg < 0 else "↑"
        else:
            level = "⚪震荡"
            trend = "→"
        signals.append({
            "name": s['name'], "price": s['price'],
            "change": chg, "level": level, "trend": trend,
        })
    
    # 综合判断
    pos_count = sum(1 for s in signals if s['change'] > 0)
    neg_count = sum(1 for s in signals if s['change'] < 0)
    if pos_count >= 3:
        verdict = "四指数多红，市场整体偏强"
    elif neg_count >= 3:
        verdict = "四指数多绿，市场整体偏弱"
    else:
        verdict = "指数分化，结构性行情"
    
    return {"indices": signals, "verdict": verdict}

# ──────────────────────────────────────────────
# 量价证伪位分析（核心工具）
# 三栏：量相对MAV5 | 关键位动作 | 一句话定性
# ──────────────────────────────────────────────

CACHE_DIR = f"{WORKSPACE}/stock-signals/cache"

def _read_cache_volumes(code):
    """读价格缓存，返回最近6个交易日成交量列表（手），用于算MAV5"""
    # code 是6位数字，找缓存文件（前缀 sh/sz）
    for prefix in ("sh", "sz"):
        path = f"{CACHE_DIR}/{prefix}{code}"
        if os.path.exists(path):
            break
    else:
        return None
    try:
        vols = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    vol = float(parts[1])
                    if vol > 0:
                        vols.append(vol)
        if len(vols) >= 2:
            return vols
        return None
    except:
        return None

def compute_volume_verdict(code, current_volume):
    """
    量相对判断：对比MAV5（近5日成交均量）
    返回: "放" | "平" | "缩"
    """
    vols = _read_cache_volumes(code)
    if vols is None or len(vols) < 3:
        return "-"
    # MAV5：取最近5个交易日成交量（不含当天）
    past = vols[-5:] if len(vols) >= 5 else vols
    mav5 = sum(past) / len(past)
    if mav5 == 0:
        return "-"
    ratio = current_volume / mav5
    if ratio >= 1.5:
        return "放"
    elif ratio <= 0.6:
        return "缩"
    else:
        return "平"

def compute_key_level(code, name, price, prev_close, high, low, holdings, focus_codes, stocks):
    """
    关键位动作判断：突破/回踩/破位
    返回: (关键位描述, 动作)
    """
    # 从 holdings 或 focus 取成本/止损/目标
    entry = None
    stop = None
    target = None
    
    # 从 holdings
    if code in holdings:
        h = holdings[code]
        entry = h.get('cost', 0)
        stop = entry * 0.95 if entry else 0
    
    # 从 focus_watchlist
    if not entry:
        try:
            fj = f"{WORKSPACE}/stock-signals/focus_watchlist.json"
            if os.path.exists(fj):
                dd = json.load(open(fj))
                for item in dd.get('focus_list', []):
                    if item.get('code') == code:
                        entry = item.get('entry_low') or item.get('entry_high') or 0
                        stop = item.get('stop_loss') or 0
                        target = item.get('target') or 0
                        break
        except:
            pass
    
    # 从 stocks 拿 MA20
    s = stocks.get(code)
    if s:
        chg = s.get('change_pct', 0)
    else:
        chg = 0
    
    desc = ""
    
    # 前高突破检测：当前价 > 近期最高（用高/收比近似）
    if high > 0 and prev_close > 0:
        # 突破前高/今日创新高: 今日高 > 前收 * 1.03 且涨幅>2%
        if high > prev_close * 1.03 and chg > 2:
            desc = "突破前高"
        # 破位/跌破: 今日低 < 前收 * 0.97 且跌幅<-2%
        elif low < prev_close * 0.97 and chg < -2:
            desc = "破位"
        # 回踩MA20或成本价
        elif stop and price >= stop and price <= entry * 1.02 if entry else False:
            desc = "回踩成本"
        elif entry and 0 < entry and abs(price - entry) / price < 0.02:
            desc = "成本附近"
    
    if not desc:
        # 兜底：看价格相对前收
        if chg > 2:
            desc = "上冲"
        elif chg < -2:
            desc = "下探"
        else:
            desc = "窄幅"
    
    return desc

def compute_vp_verdict(code, name, price, prev_close, high, low, current_volume, holdings, focus_codes, stocks):
    """
    完整量价证伪位判定
    返回: {"vol_rel": "放/平/缩", "key_level": "描述", "verdict": "推进/试探/洗盘/派发松动/弱势反抽"}
    """
    vol_rel = compute_volume_verdict(code, current_volume)
    key_level = compute_key_level(code, name, price, prev_close, high, low, holdings, focus_codes, stocks)
    
    s = stocks.get(code, {})
    chg = s.get('change_pct', 0)
    
    # ── 一句话定性（核心判断逻辑）──
    # 推进：放量上涨/突破关键位
    if vol_rel == "放" and chg >= 1.5 and ("突破" in key_level or "上冲" in key_level):
        verdict = "推进"
    # 试探：到压力位但量不确认（放量但没涨、或缩量到压力）
    elif ("前高" in key_level or "成本附近" in key_level or "上冲" in key_level) and vol_rel in ("平", "缩"):
        verdict = "试探"
    # 洗盘：回调支撑缩量
    elif vol_rel == "缩" and chg <= -1 and ("回踩" in key_level or "下探" in key_level or "成本附近" in key_level):
        verdict = "洗盘"
    # 派发/松动：关键位放量不守（放量但跌，或放量滞涨）
    elif vol_rel == "放" and chg <= -2:
        verdict = "派发/松动"
    elif vol_rel == "放" and abs(chg) < 1:
        verdict = "派发/松动"
    # 弱势反抽：反弹但量不行
    elif vol_rel == "缩" and chg >= 0 and ("上冲" in key_level or "窄幅" in key_level):
        verdict = "弱势反抽"
    else:
        # 默认
        if chg > 2:
            verdict = "推进"
        elif chg < -2:
            verdict = "派发/松动"
        else:
            verdict = "试探"
    
    return {"vol_rel": vol_rel, "key_level": key_level, "verdict": verdict}

# ──────────────────────────────────────────────

def compute_l2_holdings(stocks, holdings):
    """L2: 持仓监控"""
    alerts = []
    for code, info in holdings.items():
        s = stocks.get(code)
        if not s: continue
        
        chg = s['change_pct']
        cost = info['cost']
        price = s['price']
        pl_pct = (price - cost) / cost * 100
        pl = round((price - cost) * info['shares'])
        stop_price = round(cost * 0.95, 2)
        
        # 异动判断
        warning = ""
        if price <= stop_price:
            warning = "🔴止损逼近/已破！"
        elif pl_pct <= -3:
            warning = "🟢浮亏超3%"
        elif pl_pct >= 10:
            warning = "🔴浮盈超10%，注意止盈"
        elif abs(chg) >= 3:
            warning = "⚡盘中异动"
        
        # 量价证伪
        vp = compute_vp_verdict(code, info['name'], price, s['prev_close'], s['high'], s['low'], s['volume'], holdings, {}, stocks)
        
        alerts.append({
            "name": info['name'], "code": code,
            "price": price, "change": chg,
            "cost": cost, "shares": info['shares'],
            "pl_pct": round(pl_pct, 2), "pl": pl,
            "stop_price": stop_price, "warning": warning,
            "vol_rel": vp["vol_rel"],
            "key_level": vp["key_level"],
            "vp_verdict": vp["verdict"],
        })
    return alerts

def compute_l3_focus(stocks, focus_codes, holdings, l6_data=None):
    """L3: 重点关注标的（focus_watchlist.json + 静态池 + 风口）"""
    # 从 focus_watchlist.json 读取完整的重点关注清单
    FOCUS_ENTRY = {}
    focus_json_path = f"{WORKSPACE}/stock-signals/focus_watchlist.json"
    if os.path.exists(focus_json_path):
        try:
            with open(focus_json_path) as ff:
                focus_data = json.load(ff)
            for item in focus_data.get('focus_list', []):
                code = item.get('code', '')
                if not code:
                    continue
                # 跳过已清仓标的（hold=False 或 status含"清仓"）
                if not item.get('hold', True) or '清仓' in item.get('status', ''):
                    continue
                entry_val = item.get('entry_low', 0) or item.get('entry_high', 0)
                stop_val = item.get('stop_loss', 0)
                target_val = item.get('target', 0)
                status = item.get('status', '监控')
                signals = '; '.join(item.get('signals', []))
                note = item.get('note', '')
                catalyst = item.get('catalyst', '')[:30]
                FOCUS_ENTRY[code] = {
                    "entry": entry_val if entry_val > 0 else 0,
                    "stop": stop_val if stop_val > 0 else 0,
                    "target": target_val if target_val > 0 else 0,
                    "note": f"{status}|{catalyst}{'|'+signals if signals else ''}",
                    "is_focus": True,
                }
        except Exception as e:
            pass  # fallback to static
    
    # 静态池作为兜底（补一些可能在focus里没有的）
    STATIC_FOCUS = {
        "300660": {"entry": 48.06, "stop": 44.60, "target": 53.89, "note": "雷利-站稳介入"},
        "002938": {"entry": 100.0, "stop": 82.0, "target": 130.0, "note": "鹏鼎-回踩100-102介入"},
        "002881": {"entry": 49.14, "stop": 41.49, "target": 53.0, "note": "美格-突破介入"},
        "000636": {"entry": 34.0, "stop": 32.0, "target": 40.0, "note": "风华-回调34-35介入"},
    }
    
    # focus_watchlist.json 优先级高于静态池，但静态池补漏
    for code, rule in STATIC_FOCUS.items():
        if code not in FOCUS_ENTRY:
            FOCUS_ENTRY[code] = rule
    
    signals = []
    for code, rule in FOCUS_ENTRY.items():
        s = stocks.get(code)
        if not s: continue
        price = s['price']
        entry = rule['entry']
        stop = rule['stop']
        
        is_holding = code in holdings
        triggered = price >= entry
        
        # 量价证伪
        vp = compute_vp_verdict(code, s['name'], price, s['prev_close'], s['high'], s['low'], s['volume'], holdings, focus_codes, stocks)
        
        signals.append({
            "name": s['name'], "code": code,
            "price": price, "change": s['change_pct'],
            "entry": entry, "stop": stop, "target": rule['target'],
            "triggered": triggered, "gap_pct": round((price-entry)/entry*100, 2),
            "is_holding": is_holding, "note": rule['note'],
            "vol_rel": vp["vol_rel"],
            "key_level": vp["key_level"],
            "vp_verdict": vp["verdict"],
        })
    
    # === L6热点回流 ===
    if l6_data:
        for alert in l6_data.get('buyable_alerts', []):
            code = alert['code']
            # 跳过已在持仓或静态重点池里的
            if code in holdings or code in {s['code'] for s in signals}:
                continue
            if alert['composite_score'] < 3.5:
                continue  # 评分太低不纳入重点
            signals.append({
                "name": alert['name'], "code": code,
                "price": alert['price'], "change": alert['p0_total'],  # 用P0总分替代change展示
                "entry": alert['price'], "stop": round(alert['price'] * 0.95, 2),
                "target": round(alert['price'] * 1.15, 2),
                "triggered": True, "gap_pct": 0.0,
                "is_holding": False,
                "note": f"L6热点升级|{alert.get('sector','')}|评分{alert['composite_score']}|{alert.get('reason','')[:30]}",
            })
    
    # === 降级：3天无活跃则移除 ===
    # (逻辑在hot_monitor.json中维护，此处仅写入输出)

    # === 风口研报回流 ===
    fengkou_path = f"{WORKSPACE}/stock-signals/fengkou_candidates.json"
    if os.path.exists(fengkou_path):
        try:
            with open(fengkou_path) as ff:
                fengkou = json.load(ff)
            import datetime
            today_str = datetime.datetime.now().strftime("%Y-%m-%d")
            existing_codes = {s['code'] for s in signals}
            for c in fengkou.get('candidates', []):
                code = c['code']
                if code in existing_codes:
                    continue  # 已在池中
                name = c.get('name', code)
                entry = c.get('entry_low', 0)
                catalyst = c.get('catalyst', '')[:40]
                existing_codes.add(code)
                signals.append({
                    "name": name, "code": code,
                    "price": stocks.get(code, {}).get('price', 0),
                    "change": stocks.get(code, {}).get('change_pct', 0),
                    "entry": entry, "stop": round(entry * 0.95, 2) if entry else 0,
                    "target": round(entry * 1.15, 2) if entry else 0,
                    "triggered": False, "gap_pct": 0,
                    "is_holding": False,
                    "note": f"风口研报推荐|{catalyst}",
                })
        except Exception as e:
            pass

    return signals

def compute_l4_etf(stocks):
    """L4: ETF+概念板块"""
    etf_signals = []
    for code, info in ETF.items():
        s = stocks.get(code[2:])
        if not s: continue
        chg = s['change_pct']
        if abs(chg) >= 2:
            level = "⚡异动" if abs(chg) < 5 else "🔥大涨" if chg > 0 else "🟢大跌"
        else:
            level = "正常"
        etf_signals.append({
            "name": s['name'], "price": s['price'],
            "change": chg, "level": level,
        })
    
    # 概念板块：用代表性个股近似
    concept_signals = []
    CONCEPT_MAP = {
        "HBM": ["688008","002049"],
        "CPO": ["300394","300502","002281"],
        "脑机接口": ["300003"],
        "商业航天": ["600118","002025","300045","688568"],
    }
    for concept, codes in CONCEPT_MAP.items():
        chgs = []
        names = []
        for code in codes:
            s = stocks.get(code)
            if s:
                chgs.append(s['change_pct'])
                names.append(s['name'])
        if chgs:
            avg_chg = round(sum(chgs) / len(chgs), 2)
            if abs(avg_chg) >= 2:
                concept_signals.append({
                    "name": concept, "avg_change": avg_chg,
                    "stocks": names, "level": "⚡"
                })
    
    return {"etf": etf_signals, "concept": concept_signals}

def compute_l5_watchlist(stocks, watchlist_codes, holdings):
    """L5: 常规自选异动（±2%以上）"""
    signals = []
    holdings_codes = set(holdings.keys())
    focus_set = {"000969","300660","002938","002881","000988","000636"}
    
    for code in watchlist_codes:
        s = stocks.get(code)
        if not s or not s['price']: continue
        # 跳过已在前4层处理过的
        if code in holdings_codes or code in focus_set:
            continue
        
        chg = abs(s['change_pct'])
        if chg < 2: continue  # 只记录±2%以上的
        
        direction = "🔴" if s['change_pct'] > 0 else "🟢"
        signals.append({
            "name": s['name'], "code": code,
            "price": s['price'], "change": s['change_pct'],
            "direction": direction,
        })
    
    # 按涨幅绝对值排序
    signals.sort(key=lambda x: abs(x['change']), reverse=True)
    return signals

# ========== 主流程 ==========

def main():
    t_start = time.time()
    
    # 1. 批量采集
    stocks, fetch_time, holdings, watchlist_codes, focus_codes = fetch_batch_prices()
    
    # 2. 分层计算
    l1 = compute_l1_market(stocks)
    l2 = compute_l2_holdings(stocks, holdings)
    l4 = compute_l4_etf(stocks)
    l5 = compute_l5_watchlist(stocks, watchlist_codes, holdings)
    
    total_time = round(time.time() - t_start, 2)
    
    # L6热点异动分析（先算，L3需要回流）
    l6 = compute_l6_hot(l5, l4['etf'])
    
    # L3重点监控（带L6热点回流）
    l3 = compute_l3_focus(stocks, focus_codes, holdings, l6)
    
    # 3. 写入signal文件
    data = {
        "timestamp": datetime.now().strftime("%H:%M:%S"),
        "fetch_time": fetch_time,
        "total_time": total_time,
        "total_stocks": len(stocks),
        "L1_market": l1,
        "L2_holdings": l2,
        "L3_focus": l3,
        "L4_etf_concept": l4,
        "L5_watchlist": l5,
        "L6_hot": l6,
    }
    
    # JSON完整数据
    with open(f"{ALERT_DIR}/all_signals.json", 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    # L1 可读文本
    with open(f"{ALERT_DIR}/L1_market.md", 'w') as f:
        for idx in l1['indices']:
            f.write(f"{idx['name']} {idx['price']} {idx['trend']}({idx['change']:+.2f}%) {idx['level']}\n")
        f.write(f"判断：{l1['verdict']}\n")
        f.write(f"[采集耗时{fetch_time}s | {len(stocks)}只]\n")
    
    # L2 可读文本
    with open(f"{ALERT_DIR}/L2_holdings.md", 'w') as f:
        if l2:
            for h in l2:
                warn_tag = f" {h['warning']}" if h['warning'] else ""
                vol_rel = h.get('vol_rel', '-')
                key_level = h.get('key_level', '-')
                vp_v = h.get('vp_verdict', '-')
                f.write(f"{h['name']}({h['code']}) {h['price']}元 {h['change']:+.2f}% | 持仓{h['shares']}股@{h['cost']:.2f} 浮盈{h['pl']:+d}({h['pl_pct']:+.2f}%) | 止损{h['stop_price']}{warn_tag}\n")
                f.write(f"    量价证伪: {vol_rel}/{key_level}/{vp_v}\n")
        else:
            f.write("无持仓\n")
    
    # L3 可读文本
    with open(f"{ALERT_DIR}/L3_focus.md", 'w') as f:
        for foc in l3:
            if foc['is_holding']:
                label = "【持仓】"
            elif foc['triggered']:
                label = "【监控·触发】"
            else:
                label = "【监控·等待】"
            status = "✅已介入" if foc['is_holding'] else ("🔵触发" if foc['triggered'] else "⏳等待")
            vol_rel = foc.get('vol_rel', '-')
            key_level = foc.get('key_level', '-')
            vp_v = foc.get('vp_verdict', '-')
            f.write(f"{label}{foc['name']}({foc['code']}) {foc['price']}元 {foc['change']:+.2f}% | 介入{foc['entry']} 现距{status}({foc['gap_pct']:+.2f}%) | 止损{foc['stop']} 目标{foc['target']}\n")
            f.write(f"    量价证伪: {vol_rel}/{key_level}/{vp_v}\n")
    
    # L4 可读文本
    with open(f"{ALERT_DIR}/L4_etf_concept.md", 'w') as f:
        f.write("【ETF】\n")
        for e in l4['etf']:
            f.write(f"{e['name']} {e['price']}元 ({e['change']:+.2f}%) {e['level']}\n")
        f.write("【概念板块】\n")
        for c in l4['concept']:
            f.write(f"{c['name']} 平均{c['avg_change']:+.2f}% {c['level']}\n")
    
    # L5 可读文本
    with open(f"{ALERT_DIR}/L5_watchlist.md", 'w') as f:
        if l5:
            for w in l5:
                f.write(f"{w['direction']}{w['name']}({w['code']}) {w['price']}元 ({w['change']:+.2f}%)\n")
        else:
            f.write("无±2%以上异动\n")
    
    # L2+L3+L4+L5 合并信号（供推送层决定是否推送）
    urgent_signals = []
    # 持仓止损
    for h in l2:
        if h['warning']:
            urgent_signals.append(f"【持仓】{h['name']} {h['warning']}")
    # 重点触发
    for f in l3:
        if f['triggered'] and not f['is_holding']:
            urgent_signals.append(f"【重点】{f['name']} 已触发介入条件({f['entry']})!")
    # ETF异动
    for e in l4['etf']:
        if '大涨' in e['level'] or '大跌' in e['level']:
            urgent_signals.append(f"【ETF】{e['name']} {e['change']:+.2f}% {e['level']}")
    # 自选大涨
    for w in l5[:3]:
        if abs(w['change']) >= 8:
            # 区分涨停板：主板10%，创业板/科创板20%
            code = w.get('code', '')
            is_20pct_board = code.startswith('30') or code.startswith('688')
            if is_20pct_board:
                threshold_approach = 18.0  # 距涨停20%差2%视为涨停级
            else:
                threshold_approach = 9.5   # 距涨停10%差0.5%视为涨停级
            if abs(w['change']) >= threshold_approach:
                label = "🔥涨停级"
            else:
                label = "大异动"
            urgent_signals.append(f"【自选】{w['name']}({code}) {w['change']:+.2f}% {label}")
    
    with open(f"{ALERT_DIR}/urgent.md", 'w') as f:
        for s in urgent_signals:
            f.write(s + '\n')
        if not urgent_signals:
            f.write("无紧急信号\n")
    
    # 生成信号引擎摘要（供盘中监控cron使用，替代189KB原始文件）
    sig_summary = f"{ALERT_DIR}/signals_summary.json"
    subprocess.run(["python3", f"{WORKSPACE}/scripts/signals_summary.py"],
                   capture_output=True, timeout=10)
    
    # V2.2 板块资金流向监控（新增）
    subprocess.run(["python3", f"{WORKSPACE}/scripts/sector_fund_flow.py"],
                   capture_output=True, timeout=10)
    
    # V2.2 产业链传导模型（新增，依赖sector_fund_flow输出）
    subprocess.run(["python3", f"{WORKSPACE}/scripts/industry_chain.py"],
                   capture_output=True, timeout=10)
    
    print(f"✅ {datetime.now().strftime('%H:%M:%S')} 分层采集完成 | {len(stocks)}只 | 采集{fetch_time}s | 总计{total_time}s")
    print(f"   持仓{l2}| 重点{l3}| ETF+概念{l4['etf']}| 自选异动{len(l5)} | 信号摘要{os.path.getsize(sig_summary) if os.path.exists(sig_summary) else 'N/A'}B")
    print(f"   板块强度{os.path.getsize(f'{ALERT_DIR}/sector_flow.json')}B | 传导预警{os.path.getsize(f'{ALERT_DIR}/chain_alerts.json')}B")
    return data

if __name__ == "__main__":
    main()
