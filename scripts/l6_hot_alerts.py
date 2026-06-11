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
    """加载引擎信号
    优先从 engine_signals.json 读取
    回退到从 all_signals.json 中提取
    """
    path = f"{ALERT_DIR}/engine_signals.json"
    if os.path.exists(path):
        try:
            with open(path) as f:
                data = json.load(f)
            if isinstance(data, list):
                return {s['code']: s for s in data}
            elif isinstance(data, dict):
                return data
        except:
            pass
    
    # 回退：从 all_signals.json 提取
    fallback = f"{ALERT_DIR}/all_signals.json"
    if os.path.exists(fallback):
        try:
            with open(fallback) as f:
                all_data = json.load(f)
            # 从L3信号中提取引擎评分
            result = {}
            for s in all_data.get('L3_focus', []):
                code = s.get('code', '')
                if code:
                    result[code] = {
                        'code': code,
                        'total_score_ext': s.get('total_score', 0),
                        'quality_score': s.get('quality_score', 0),
                        'morph_score': s.get('morph_score', 0),
                        'price_level': s.get('level', 'L0_NORMAL'),
                        'resonance': {'verdict': s.get('verdict', '未知')},
                        'signals': [],
                    }
            return result
        except:
            pass
    
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
        
        # P0评分（从引擎信号或L3回退数据中读取）
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
        # 没有引擎信号时，用异动幅度+板块热度估算（回退值偏乐观，因为异动本身就是信号）
        if total_score == 0 and not sig:
            total_score = min(chg / 3, 3.5)  # 5%异动≈1.67分, 10%+≈3.33分
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
        if not key_signals:
            direction_emoji = s['direction']
            key_signals.append(f"{direction_emoji}{abs(chg):.1f}%异动·{sector}")
        
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
            # 涨幅大但评分低 → 如果板块热且涨幅适中(5-8%)，仍可关注
            if is_hot_sector and chg <= 8:
                is_buyable = True
                reason = f"板块热点+涨幅{chg:.1f}%，关注分歧低吸"
            elif chg <= 7:
                is_buyable = True
                reason = f"涨幅{chg:.1f}%适中，关注次日确认"
            else:
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
    
    # ── 降级逻辑 ──
    # 条件：在热点池但今日无±3%以上异动 → 标记非活跃天数
    # 连续3天非活跃 → 自动移除
    # 评分归零(0.0)且非今日异动 → 立即移除
    today_active = {a['code'] for a in hot_alerts}
    stale = []
    for code, info in list(hot_monitor["stocks"].items()):
        if code not in today_active:
            inactive_days = info.get('inactive_days', 0) + 1
            info['inactive_days'] = inactive_days
            # 评分0.0且连续3天非活跃 → 移除（给新标的留缓冲）
            if info.get('total_score', 0) == 0.0 and inactive_days >= 3:
                stale.append(code)
            # 连续5天非活跃 → 移除
            elif inactive_days >= 5:
                stale.append(code)
        else:
            # 今日活跃 → 重置非活跃计数
            info['inactive_days'] = 0
            # 更新评分
            for a in hot_alerts:
                if a['code'] == code:
                    info['total_score'] = a['p0_total']
                    break
    for code in stale:
        name = hot_monitor["stocks"][code].get('name', code)
        del hot_monitor["stocks"][code]
    
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
    # 先保存hot_monitor（降级逻辑已执行，即使无今日异动也要保存）
    save_hot_monitor(hot_monitor)
    
    with open(f"{ALERT_DIR}/L6_hot_alerts.md", 'w') as f:
        if not hot_alerts:
            f.write("今日无±5%以上异动\n")
            sync_to_focus_watchlist(buyable, hot_monitor)
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
    
    # ── 同步到 focus_watchlist.json（升级+降级）──
    sync_result = sync_to_focus_watchlist(buyable, hot_monitor)
    
    # ── 生成每日升降级通报 ──
    generate_daily_report(result, sync_result, hot_monitor)
    
    return result

def sync_to_focus_watchlist(buyable_alerts, hot_monitor=None):
    """
    将L6可参与热点标的同步到 focus_watchlist.json
    - 综合评分≥3.5且不在池中的 → 自动添加（带L6热点升级标签）
    - 已在池中的 → 更新评分和催化描述
    - 已清仓且无活跃异动的 → 自动移除
    - L6热点升级标的连续5天非活跃 → 自动移除
    """
    ws = os.environ.get('WORKSPACE', '/root/.openclaw/workspace')
    focus_path = f"{ws}/stock-signals/focus_watchlist.json"
    if not os.path.exists(focus_path):
        return
    
    try:
        with open(focus_path) as f:
            focus = json.load(f)
    except:
        return
    
    focus_list = focus.get('focus_list', [])
    existing_codes = {s['code'] for s in focus_list}
    today = datetime.now().strftime("%Y-%m-%d")
    
    # 读取当前持仓（从TOOLS.md）
    # 从focus_list中读hold标记
    holdings = {s['code'] for s in focus_list if s.get('hold')}
    
    # ── 升级：L6热点标的自动添加 ──
    added = 0
    for a in buyable_alerts:
        code = a['code']
        if code in existing_codes:
            # 已在池中 → 更新评分和催化
            for s in focus_list:
                if s['code'] == code:
                    if 'catalyst' in s and s['catalyst']:
                        if 'L6热点' not in s['catalyst']:
                            s['catalyst'] += f" | L6热点评分{a['composite_score']}"
                    break
            continue
        if code in holdings:
            continue  # 持仓中不重复添加
        if a['composite_score'] < 3.5:
            continue  # 评分太低不纳入
        
        # 新加入
        focus_list.append({
            "code": code,
            "name": a['name'],
            "stars": 3,
            "hold": False,
            "status": "L6热点升级",
            "catalyst": f"L6热点异动|{a.get('sector','')}|综合评分{a['composite_score']}|{a.get('reason','')[:40]}",
            "entry_low": None,
            "entry_high": None,
            "stop_loss": round(a['price'] * 0.95, 2),
            "target": round(a['price'] * 1.15, 2),
            "signals": a.get('key_signals', [])[:3],
            "note": f"{today} L6热点自动升级，评分{a['composite_score']}"
        })
        existing_codes.add(code)
        added += 1
    
    # ── 降级：已清仓 / L6热点过期 / 低星已清仓 的标的移除 ──
    removed = 0
    
    # 从hot_monitor获取非活跃信息
    hot_inactive = {}
    if hot_monitor:
        for code, info in hot_monitor.get('stocks', {}).items():
            hot_inactive[code] = info.get('inactive_days', 0)
    
    focus_list_new = []
    for s in focus_list:
        code = s['code']
        status = s.get('status', '')
        hold = s.get('hold', False)
        stars = s.get('stars', 0)
        
        # 保留条件：持仓中
        if hold:
            focus_list_new.append(s)
            continue
        
        # L6热点升级标的：连续5天非活跃 → 移除
        if 'L6热点' in status:
            inactive = hot_inactive.get(code, 0)
            if inactive >= 5:
                removed += 1
                continue
            focus_list_new.append(s)
            continue
        
        # 低星已清仓 → 移除
        if '已清仓' in status and stars < 3:
            removed += 1
            continue
        
        # 高星已清仓 → 保留但标记为历史
        if '已清仓' in status and stars >= 3:
            focus_list_new.append(s)
            continue
        
        focus_list_new.append(s)
    
    focus['focus_list'] = focus_list_new
    focus['last_update'] = today
    
    with open(focus_path, 'w') as f:
        json.dump(focus, f, ensure_ascii=False, indent=2)
    
    if added or removed:
        log_msg = f"[L6升降级] 升级+{added} | 降级-{removed} | 池内{len(focus_list_new)}只"
        print(log_msg)
        # 写入日志
        log_path = f"{ALERT_DIR}/l6_upgrade_log.txt"
        with open(log_path, 'a') as f:
            f.write(f"{today} {log_msg}\n")
    
    return {"added": added, "removed": removed, "total": len(focus_list_new)}


def generate_daily_report(l6_result, sync_result, hot_monitor):
    """生成每日升降级通报文件"""
    today = datetime.now().strftime("%Y-%m-%d")
    report_path = f"{ALERT_DIR}/L6_daily_report.md"
    
    lines = []
    lines.append(f"# L6热点升降级日报 | {today}")
    lines.append("")
    
    # 热门板块
    if l6_result['hot_sectors']:
        lines.append(f"🔥 今日热门板块: {' / '.join(l6_result['hot_sectors'])}")
        lines.append("")
    
    # 异动概况
    lines.append(f"## 异动概况")
    lines.append(f"- L5自选池扫描: {l6_result['total_hot']}只±5%+异动")
    lines.append(f"- 可参与标的: {l6_result['total_buyable']}只")
    lines.append(f"- 热点监控池: {len(hot_monitor.get('stocks', {}))}只")
    lines.append("")
    
    # 升级
    if sync_result['added'] > 0:
        lines.append(f"## ⬆️ 升级（+{sync_result['added']}只进入重点关注池）")
        for a in l6_result.get('buyable_alerts', []):
            if a['composite_score'] >= 3.5:
                lines.append(f"- **{a['name']}**({a['code']}) {a['price']}元 {a['change']:+.2f}% | 评分{a['composite_score']} | {a['sector']}")
                lines.append(f"  → {a['reason']}")
        lines.append("")
    
    # 降级
    if sync_result['removed'] > 0:
        lines.append(f"## ❌ 降级（-{sync_result['removed']}只移出重点关注池）")
        lines.append(f"- 已清仓低星标的 / L6热点过期自动移除")
        lines.append("")
    
    # 当前热点监控池
    stocks = hot_monitor.get('stocks', {})
    if stocks:
        lines.append(f"## 📊 热点监控池（{len(stocks)}只）")
        lines.append("")
        lines.append("| 代码 | 名称 | 评分 | 非活跃天 | 板块 |")
        lines.append("|:-----|:-----|:---:|:------:|:-----|")
        for code, info in sorted(stocks.items()):
            score = info.get('total_score', 0)
            inactive = info.get('inactive_days', 0)
            sector = info.get('sector', '')
            name = info.get('name', code)
            warn = '⚠️' if inactive >= 3 else ''
            lines.append(f"| {code} | {name}{warn} | {score:.1f} | {inactive} | {sector} |")
        lines.append("")
    
    # 池内统计
    lines.append(f"## 📋 重点关注池统计")
    lines.append(f"- 当前池内: {sync_result['total']}只")
    lines.append(f"- 今日升级: +{sync_result['added']}只")
    lines.append(f"- 今日降级: -{sync_result['removed']}只")
    lines.append("")
    
    with open(report_path, 'w') as f:
        f.write('\n'.join(lines))
    
    print(f"[L6日报] 已写入 {report_path}")


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
