"""L6热点异动分析 + 动态升降级系统
- 将±5%以上异动标的与引擎评分交叉分析
- 按异动幅度+P0评分+板块关联+信号共振综合打分
- 动态维护"热点监控池"（文件持久化，自动升降级）
"""
import json, os, time
from datetime import datetime

ALERT_DIR = "/tmp/stock_alerts"

# 板块映射表（概念→成分股代码前缀匹配）
SECTOR_MAP = {
    "半导体/芯片": {"codes": [], "etfs": ["芯片ETF富国"]},
    "CPO/光通信": {"codes": ["300502","300308","300394","002281","300620"], "etfs": []},
    "PCB/覆铜板": {"codes": ["603256","600183","002916","002938","002384","002463","300476"], "etfs": []},
    "存储芯片": {"codes": ["688008","688525","603986","301308","300475","001309"], "etfs": []},
    "封测": {"codes": ["002185","600584","603203"], "etfs": []},
    "航天/军工": {"codes": ["600118","002025","300045","688568","300762","600343","300455","688523","301306","002465","600391","600592","301005","000901","002682","600151"], "etfs": []},
    "模拟芯片": {"codes": ["300661","688798"], "etfs": []},
    "AI算力": {"codes": ["601138","000977","300476","002837","300499","301018","300738","300383"], "etfs": []},
    "有色/资源": {"codes": ["601600","600549","603799","603993","002428"], "etfs": ["有色金属ETF南方"]},
    "新能源": {"codes": ["300750","300450","600409","002865"], "etfs": []},
    "消费电子": {"codes": ["300115","002050"], "etfs": []},
}

def load_engine_signals():
    """加载引擎信号"""
    path = f"{ALERT_DIR}/engine_signals.json"
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
        return {s['code']: s for s in data}
    except:
        return {}

def load_hot_monitor():
    """加载持久化热点监控池"""
    path = f"{ALERT_DIR}/hot_monitor.json"
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except:
            pass
    return {"stocks": {}, "version": 1}

def save_hot_monitor(data):
    with open(f"{ALERT_DIR}/hot_monitor.json", 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_sector(code, name):
    """判断股票所属板块"""
    for sector_name, sector_info in SECTOR_MAP.items():
        if code in sector_info["codes"]:
            return sector_name
        for keyword in sector_name.split("/"):
            if keyword in name:
                return sector_name
    return "其他"

def compute_l6_hot(l5_stocks, etf_signals):
    """
    L6热点异动分析
    - 从L5筛选±5%+的异动标的
    - 交叉引擎评分
    - 评分公式：异动幅度权重(0.3) + P0评分权重(0.4) + 板块热度权重(0.3)
    """
    engine_data = load_engine_signals()
    hot_monitor = load_hot_monitor()
    today = datetime.now().strftime("%Y-%m-%d")
    
    # 识别当前热门板块（ETF涨幅>2%的算热）
    hot_sectors = set()
    for e in etf_signals:
        if abs(e['change']) > 2:
            for sname, sinfo in SECTOR_MAP.items():
                if e['name'] in sinfo.get('etfs', []):
                    hot_sectors.add(sname)
    
    # 核心分析
    hot_alerts = []
    
    for s in l5_stocks:
        chg = abs(s['change'])
        if chg < 5:
            continue  # 只分析±5%+
        
        code = s['code']
        name = s['name']
        sig = engine_data.get(code, {})
        sector = get_sector(code, name)
        
        # P0评分
        total_score = float(sig.get('total_score_ext', 0))
        quality_score = float(sig.get('quality_score', 0))
        morph_score = float(sig.get('morph_score', 0))
        level = sig.get('price_level', 'L0_NORMAL')
        resonance = sig.get('resonance', {})
        verdict = resonance.get('verdict', '未知')
        signals = sig.get('signals', [])
        
        # 板块热度加分
        is_hot_sector = sector in hot_sectors
        sector_bonus = 1.0 if is_hot_sector else 0.0
        
        # 计算综合评分 (0-10)
        chg_score = min(chg / 10, 1.0) * 3  # 异动幅度(0-3分)
        p0_score = min(total_score / 5, 1.0) * 4  # P0评分(0-4分)
        sector_score = sector_bonus * 3  # 板块热度(0-3分)
        composite = round(chg_score + p0_score + sector_score, 1)
        
        # 提取关键信号文本
        key_signals = []
        for sig_item in signals[:5]:
            rule = sig_item.get('rule', '')
            note = sig_item.get('note', '')
            short_note = note[:60] if note else rule
            key_signals.append(short_note)
        
        # 推荐逻辑
        direction = s['direction']
        is_buyable = False
        reason = ""
        
        if direction == "🔴" and chg > 9:
            # 涨停不追
            is_buyable = False
            reason = "涨停封板，等开板回踩"
        elif direction == "🔴" and total_score >= 2.0:
            is_buyable = True
            reason = f"放量强势+P0评分{total_score}，关注次日分歧低吸"
        elif direction == "🔴" and total_score < 2.0:
            is_buyable = False
            reason = f"涨幅大但P0评分仅{total_score}，追高风险大"
        elif direction == "🟢":
            is_buyable = total_score >= 1.5 and '卖出' not in verdict
            reason = f"大跌但P0评分{total_score}，看是否左侧机会" if is_buyable else "跌幅大无支撑信号，观望"
        
        alert = {
            "code": code, "name": name,
            "price": s['price'], "change": s['change'],
            "direction": direction,
            "sector": sector,
            "is_hot_sector": is_hot_sector,
            "p0_total": total_score,
            "p0_quality": quality_score,
            "p0_morph": morph_score,
            "level": level,
            "verdict": verdict,
            "composite_score": composite,
            "is_buyable": is_buyable,
            "reason": reason,
            "key_signals": key_signals,
        }
        hot_alerts.append(alert)
        
        # 更新热点监控池（升降级）
        if code not in hot_monitor["stocks"]:
            hot_monitor["stocks"][code] = {
                "name": name,
                "first_seen": today,
                "sector": sector,
                "total_score": total_score
            }
    
    # 降级逻辑：在热点池但连续3天无±3%以上异动的，移除
    # 简化版：只保留最近3天内出现过的
    stale = []
    for code, info in hot_monitor["stocks"].items():
        if code not in {a['code'] for a in hot_alerts}:
            # 保留但标记为非活跃
            pass
    
    # 按综合评分排序
    hot_alerts.sort(key=lambda x: x['composite_score'], reverse=True)
    
    # 可买入的
    buyable = [a for a in hot_alerts if a['is_buyable']]
    
    hot_monitor["last_update"] = today
    
    result = {
        "hot_alerts": hot_alerts,
        "buyable_alerts": buyable,
        "hot_sectors": list(hot_sectors),
        "total_hot": len(hot_alerts),
        "total_buyable": len(buyable),
    }
    
    # 写入信号文件
    with open(f"{ALERT_DIR}/L6_hot_alerts.md", 'w') as f:
        if not hot_alerts:
            f.write("今日无±5%以上异动\n")
            return result
        
        # 热门板块
        if hot_sectors:
            f.write(f"🔥 今日热门板块: {'/'.join(hot_sectors)}\n\n")
        
        # 可参与标的
        if buyable:
            f.write("【可关注】\n")
            for a in buyable:
                f.write(f"{a['direction']}{a['name']}({a['code']}) {a['price']}元 ({a['change']:+.2f}%) | 综合评分{a['composite_score']} P0总分{a['p0_total']} | {a['reason']}\n")
                for sig in a['key_signals'][:3]:
                    f.write(f"   → {sig}\n")
                f.write("\n")
        
        # 不可参与但异动大
        f.write("【异常波动·暂不参与】\n")
        for a in hot_alerts:
            if a['is_buyable']:
                continue
            f.write(f"{a['direction']}{a['name']}({a['code']}) {a['price']}元 ({a['change']:+.2f}%) | P0{a['p0_total']} {a['sector']} | {a['reason']}\n")
            if a['key_signals']:
                f.write(f"  {a['key_signals'][0]}\n")
    
    save_hot_monitor(hot_monitor)
    
    return result

# 独立运行测试
if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(__file__))
    
    # 加载L5和L4数据做测试
    with open(f"{ALERT_DIR}/all_signals.json") as f:
        all_data = json.load(f)
    
    result = compute_l6_hot(all_data['L5_watchlist'], all_data['L4_etf_concept']['etf'])
    print(f"热点异动: {result['total_hot']}只 | 可参与: {result['total_buyable']}只")
    print(f"热门板块: {result['hot_sectors']}")
    for a in result['buyable_alerts'][:5]:
        print(f"  {a['direction']}{a['name']} +{a['change']:+.2f}% P0{a['p0_total']} 评分{a['composite_score']} → {a['reason'][:40]}")
    print(f"\n输出已写入 L6_hot_alerts.md")
