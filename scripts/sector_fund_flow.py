#!/usr/bin/env python3
"""
板块资金流向监控 V1.0
- 基于 engine_signals.json + concept_map.json 计算板块强度
- 输出到 /tmp/stock_alerts/sector_flow.json，供 cron 和报告引用
- 零额外API依赖，离线计算
"""
import json, os, sys
from datetime import datetime
from collections import defaultdict

WORKSPACE = "/root/.openclaw/workspace"
CONCEPT_MAP_FILE = f"{WORKSPACE}/stock-signals/concept_map.json"
SIGNALS_FILE = "/tmp/stock_alerts/engine_signals.json"
OUTPUT_FILE = "/tmp/stock_alerts/sector_flow.json"
HISTORY_FILE = f"{WORKSPACE}/stock-signals/sector_history.json"  # 板块强度历史

MAX_HISTORY_DAYS = 5  # 保留最近5天历史，用于趋势判断


def load_signals():
    if not os.path.exists(SIGNALS_FILE):
        return []
    try:
        with open(SIGNALS_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except:
        return []

def load_concept_map():
    if not os.path.exists(CONCEPT_MAP_FILE):
        return {}
    try:
        with open(CONCEPT_MAP_FILE) as f:
            cm = json.load(f)
        return cm.get('concepts', {})
    except:
        return {}

def calc_sector_strength(signals, concept_map):
    """计算每个概念板块的强度指标"""
    today = datetime.now().strftime("%Y-%m-%d")
    results = {}
    
    for concept_name, info in concept_map.items():
        codes = info.get('codes', [])
        chgs = []
        volumes = []
        strong_count = 0  # 涨>2%
        weak_count = 0    # 跌>3%
        up_count = 0
        detail = []
        
        for s in signals:
            if not isinstance(s, dict):
                continue
            code = s.get('code', '')
            if code not in codes:
                continue
            try:
                chg = float(s.get('change_pct', '0').replace('%', ''))
            except:
                chg = 0
            name = s.get('name', '?')
            price = s.get('price', 0)
            p0 = float(s.get('total_score_ext', 0))
            
            chgs.append(chg)
            if chg > 0:
                up_count += 1
            if chg > 2:
                strong_count += 1
            if chg < -3:
                weak_count += 1
            detail.append({
                'code': code, 'name': name, 'chg': round(chg, 2),
                'price': price, 'p0': round(p0, 2)
            })
        
        if not chgs:
            continue
        
        avg_chg = round(sum(chgs) / len(chgs), 2)
        max_chg = round(max(chgs), 2)
        min_chg = round(min(chgs), 2)
        
        # 板块强度评级
        if avg_chg > 3 and strong_count >= 2:
            level = 'A'  # 强势领涨
        elif avg_chg > 1.5 and strong_count >= 1:
            level = 'B'  # 温和走强
        elif avg_chg > -1 and avg_chg <= 1.5:
            level = 'C'  # 中性观望
        elif avg_chg > -3:
            level = 'D'  # 弱势回调
        else:
            level = 'E'  # 恐慌性下跌
        
        # 龙头股（板块中P0最高且涨幅最大）
        detail_sorted = sorted(detail, key=lambda x: (x['chg'], x['p0']), reverse=True)
        leader = detail_sorted[0]['name'] if detail_sorted else None
        
        results[concept_name] = {
            'avg_chg': avg_chg,
            'max_chg': max_chg,
            'min_chg': min_chg,
            'up_ratio': round(up_count / len(chgs) * 100, 1),
            'strong_count': strong_count,
            'weak_count': weak_count,
            'total_count': len(chgs),
            'level': level,
            'leader': leader,
            'detail': detail_sorted,
            'desc': info.get('desc', ''),
            'updated': today
        }
    
    return results

def check_trend_change(concept_name, current, history_data):
    """检测板块趋势变化：连续走强/走弱/反转"""
    if concept_name not in history_data:
        return {'direction': 'new', 'days': 0, 'signal': ''}
    
    history = history_data.get(concept_name, [])
    if len(history) < 2:
        return {'direction': 'new', 'days': 0, 'signal': ''}
    
    # 检查连续天数趋势
    recent = history[-3:]  # 最近3天
    current_level_order = {'A': 5, 'B': 4, 'C': 3, 'D': 2, 'E': 1}
    current_score = current_level_order.get(current['level'], 3)
    
    up_streak = 0
    down_streak = 0
    for h in recent:
        h_level = h.get('level', 'C')
        h_score = current_level_order.get(h_level, 3)
        if h_score >= current_score:
            up_streak += 1
            down_streak = 0
        elif h_score < current_score:
            down_streak += 1
            up_streak = 0
    
    if up_streak >= 2:
        return {'direction': '上升', 'days': up_streak + 1, 
                'signal': f'连续{up_streak+1}日走强，关注板块启动'}
    elif down_streak >= 2:
        return {'direction': '下降', 'days': down_streak + 1,
                'signal': f'连续{down_streak+1}日走弱，注意板块退潮'}
    elif current['level'] in ['A', 'B'] and history[-1].get('level', 'C') in ['D', 'E']:
        avg_prev = history[-1].get('avg_chg', 0)
        return {'direction': '反转向上', 'days': 1,
                'signal': f'板块反转: 前日{avg_prev:+.1f}%→今日{current["avg_chg"]:+.1f}%，关注确认'}
    
    return {'direction': '持平', 'days': 0, 'signal': ''}

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE) as f:
                return json.load(f)
        except:
            pass
    return {}

def save_history(sector_data):
    """追加今日数据到历史"""
    history = load_history()
    today = datetime.now().strftime("%Y-%m-%d")
    
    for concept_name, data in sector_data.items():
        if concept_name not in history:
            history[concept_name] = []
        # 只保留关键字段
        record = {
            'date': data['updated'],
            'avg_chg': data['avg_chg'],
            'level': data['level'],
            'up_ratio': data['up_ratio'],
            'strong_count': data['strong_count']
        }
        # 去重（同一天只保留一条）
        existing = [h for h in history[concept_name] if h.get('date') != today]
        existing.append(record)
        # 只保留最近N天
        history[concept_name] = existing[-MAX_HISTORY_DAYS:]
    
    with open(HISTORY_FILE, 'w') as f:
        json.dump(history, f, indent=2, ensure_ascii=False)
    
    return history

def rank_sectors(sector_data):
    """按强度排序"""
    def sort_key(item):
        name, data = item
        level_order = {'A': 5, 'B': 4, 'C': 3, 'D': 2, 'E': 1}
        return (level_order.get(data['level'], 3), data['avg_chg'])
    
    return sorted(sector_data.items(), key=sort_key, reverse=True)

def generate_rotation_alerts(sector_data, history_data):
    """生成板块轮动预警"""
    alerts = []
    ranked = rank_sectors(sector_data)
    
    # 1. 领涨板块预警
    if ranked and ranked[0][1]['level'] in ['A', 'B']:
        leader = ranked[0]
        alerts.append({
            'type': '领涨',
            'sector': leader[0],
            'avg_chg': leader[1]['avg_chg'],
            'leader_stock': leader[1].get('leader'),
            'message': f"{leader[0]}领涨{leader[1]['avg_chg']:+.1f}%，龙头{leader[1].get('leader','?')}，关注板块启动"
        })
    
    # 2. 趋势变化预警
    for name, data in sector_data.items():
        trend = check_trend_change(name, data, history_data)
        if trend.get('signal'):
            alerts.append({
                'type': '趋势',
                'sector': name,
                'avg_chg': data['avg_chg'],
                'direction': trend['direction'],
                'days': trend['days'],
                'message': trend['signal']
            })
    
    # 3. 板块交叉预警（最强的板块vs最弱的板块）
    if len(ranked) >= 2:
        top = ranked[0]
        bot = ranked[-1]
        spread = top[1]['avg_chg'] - bot[1]['avg_chg']
        if spread > 5:
            alerts.append({
                'type': '极端分化',
                'top_sector': top[0],
                'top_chg': top[1]['avg_chg'],
                'bot_sector': bot[0],
                'bot_chg': bot[1]['avg_chg'],
                'spread': round(spread, 1),
                'message': f"板块极端分化: {top[0]}+{top[1]['avg_chg']:.1f}% vs {bot[0]}{bot[1]['avg_chg']:.1f}%"
            })
    
    return alerts

def main():
    signals = load_signals()
    if not signals:
        print("⚠️ 无信号数据")
        sys.exit(1)
    
    concept_map = load_concept_map()
    if not concept_map:
        print("⚠️ 无概念映射")
        sys.exit(1)
    
    # 计算板块强度
    sector_data = calc_sector_strength(signals, concept_map)
    
    # 保存历史
    history_data = save_history(sector_data)
    
    # 生成轮动预警
    alerts = generate_rotation_alerts(sector_data, history_data)
    
    # 整合输出
    output = {
        'updated': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'sectors': sector_data,
        'ranking': [(name, data['level'], data['avg_chg']) for name, data in rank_sectors(sector_data)],
        'alerts': alerts,
    }
    
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    
    # 打印摘要
    print(f"📊 板块强度 | {datetime.now().strftime('%H:%M')}")
    for name, level, avg_chg in output['ranking']:
        bar = '█' * max(1, int(avg_chg)) if avg_chg >= 0 else '░' * max(1, -int(avg_chg))
        color = '🔴' if avg_chg < -2 else ('🟢' if avg_chg > 2 else '⚪')
        print(f"  {color} {name:10s} [{level}] {avg_chg:+.2f}% {bar}")
    
    if alerts:
        print(f"\n🚨 轮动预警:")
        for a in alerts:
            print(f"  [{a['type']}] {a['message']}")

if __name__ == "__main__":
    main()
