"""L6热点异动分析 + 动态升降级系统 v2.0
- 将±5%以上异动标的与引擎评分交叉分析
- 按异动幅度+P0评分+板块关联+信号共振综合打分
- 动态维护"热点监控池"（文件持久化，自动升降级）
- 升级规则：门槛4.0 + 大跌拦截 + 涨停量能检查 + 板块热度辅助
- 降级规则：持仓保护 + 14天窗口 + 评分主动降级 + 冷冻条件
- 试用期机制：升级后3天观察期，期满评估转正/降级
- 降级可追溯：写入 demoted_history.json
"""
import json, os, re
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

# ============================================================
# 升级规则 v2.0（生效日期：2026-06-12）
# ============================================================

def should_upgrade_to_focus(candidate):
    """
    判断标的是否符合升级至重点关注池的条件。
    返回: (bool, reason_string)
    """
    # -------- 硬性资格筛查（一票否决） --------
    if candidate.get('in_positions'):
        return (False, "持仓中，无需重复升级")
    if candidate.get('in_focus_watchlist'):
        return (False, "已在重点关注池")

    # -------- 基础门槛 --------
    MIN_SCORE = 4.0
    MAX_UPGRADE_PER_DAY = 3  # 每日最多升级3只

    if candidate['composite_score'] < MIN_SCORE:
        return (False, f"评分{candidate['composite_score']} < {MIN_SCORE}")

    # -------- 异动幅度分类处理 --------
    chg_pct = abs(candidate['change'])

    # 情景A：大跌标的（跌幅 > 8%）
    if candidate['change'] < -8.0:
        candidate['upgrade_type'] = 'crash_watch'
        candidate['trial_period'] = True
        candidate['trial_days'] = 3
        return (False, "大跌>8%，转入大跌观察池，需人工确认")

    # 情景B：大涨标的（涨幅 > 8%）
    if candidate['change'] > 8.0:
        if candidate.get('volume_ratio', 0) < 1.2:
            return (False, f"涨幅{candidate['change']:.1f}%但量比{candidate.get('volume_ratio', 0)}<1.2，缩量涨停不追")

    # 情景C：常规异动（±5% ~ ±8%）
    # 板块热度辅助过滤
    if candidate['change'] < -5.0 and candidate.get('sector_heat', 0) < 1.5:
        return (False, "跌幅>5%但板块热度不足，暂不升级")

    # -------- 通过所有检查 --------
    candidate['upgrade_type'] = 'normal'
    candidate['trial_period'] = True
    candidate['trial_days'] = 3
    candidate['auto_stop_loss_pct'] = 0.95
    candidate['auto_target_pct'] = 1.15

    return (True, "符合升级条件")


# ============================================================
# 降级规则 v2.0（生效日期：2026-06-12）
# ============================================================

def should_demote_from_focus(item, days_since_exit, current_score, consecutive_low_score_days):
    """
    判断标的是否应从重点关注池降级。
    返回: (bool, reason, target_layer)
    target_layer: 'watchlist' 或 'cold'
    """
    # -------- 规则1：持仓保护（永不自动降级） --------
    if item.get('status') == 'owned' or item.get('hold'):
        return (False, "持仓中，保留", None)

    # -------- 规则2：低星已清仓（<3星） -> 立即移除 --------
    if item.get('stars', 0) < 3 and '已清仓' in item.get('status', ''):
        return (True, "低星已清仓，立即移除", 'watchlist')

    # -------- 规则3：高星已清仓 + 退出>14天 + 不在热点池 -> 降级 --------
    if '已清仓' in item.get('status', '') and days_since_exit > 14:
        if not item.get('in_hot_monitor', False):
            return (True, f"高星已清仓，退出{days_since_exit}天>14天，降级", 'watchlist')
        else:
            return (False, "高星已清仓但仍在热点池，保留", None)

    # -------- 规则4：L6热点升级 + 连续3天非活跃 -> 降级 --------
    if 'L6热点' in item.get('status', '') and item.get('inactive_days', 0) >= 3:
        return (True, f"L6热点升级，连续{item['inactive_days']}天非活跃，降级", 'watchlist')

    # -------- 规则5：评分 < 2.5 连续3天 -> 主动降级 --------
    LOW_SCORE_THRESHOLD = 2.5
    CONSECUTIVE_DAYS = 3
    if current_score < LOW_SCORE_THRESHOLD and consecutive_low_score_days >= CONSECUTIVE_DAYS:
        return (True, f"评分{current_score}<{LOW_SCORE_THRESHOLD}连续{CONSECUTIVE_DAYS}天，主动降级", 'watchlist')

    # -------- 规则6：冷冻条件 --------
    # 注意：avg_volume 和 consecutive_stop_loss_breaks 需要从实时数据填充
    # 字段缺失时（值为0或None）不触发冷冻，避免误杀
    if item.get('consecutive_stop_loss_breaks', 0) >= 3:
        return (True, "连续3天跌破止损，冷冻", 'cold')
    avg_vol = item.get('avg_volume', 0) or 0
    if avg_vol > 0 and avg_vol < 30_000_000 and current_score < 2.5:
        return (True, "成交额<3000万且评分<2.5，僵尸化冷冻", 'cold')

    # -------- 默认：保留 --------
    return (False, "保留", None)


# ============================================================
# 试用期管理（每日盘后运行）
# ============================================================

def manage_trial_periods(focus_list):
    """
    管理重点关注池中所有处于试用期的标的。
    每天运行一次（建议在盘后）。
    """
    today = datetime.now().strftime('%Y-%m-%d')
    to_remove = []
    promoted = []

    for item in focus_list:
        if not item.get('trial_period'):
            continue

        upgrade_date = item.get('upgrade_date', today)
        try:
            days_in_trial = (datetime.strptime(today, '%Y-%m-%d') - datetime.strptime(upgrade_date, '%Y-%m-%d')).days
        except:
            days_in_trial = 0

        if days_in_trial >= item.get('trial_days', 3):
            if item.get('composite_score', 0) >= 3.5 and not item.get('stopped_out', False):
                item['trial_period'] = False
                item['stars'] = max(item.get('stars', 0), 3)
                promoted.append(item['code'])
            else:
                to_remove.append(item)

    # 批量移除不合格标的
    focus_list[:] = [item for item in focus_list if item not in to_remove]

    if promoted or to_remove:
        print(f"[试用期] 转正{len(promoted)}只 | 淘汰{len(to_remove)}只")

    return {"promoted": promoted, "removed": [r['code'] for r in to_remove]}


# ============================================================
# 降级可追溯
# ============================================================

def record_demotion(item, reason, demoted_price, target_layer):
    """降级时写入可追溯记录"""
    today = datetime.now().strftime('%Y-%m-%d')
    history_path = f"{ALERT_DIR}/demoted_history.json"

    history = []
    if os.path.exists(history_path):
        try:
            with open(history_path) as f:
                history = json.load(f)
        except:
            pass

    history.append({
        "code": item.get('code', ''),
        "name": item.get('name', ''),
        "demoted_reason": reason,
        "demoted_date": today,
        "demoted_price": demoted_price,
        "target_layer": target_layer,
        "can_be_recovered": True,
    })

    with open(history_path, 'w') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


# ============================================================
# 工具函数
# ============================================================

def load_engine_signals():
    """加载引擎信号"""
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

    fallback = f"{ALERT_DIR}/all_signals.json"
    if os.path.exists(fallback):
        try:
            with open(fallback) as f:
                all_data = json.load(f)
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
    for sector_name, sector_info in SECTOR_MAP.items():
        if code in sector_info["codes"]:
            return sector_name
        for keyword in sector_name.split("/"):
            if keyword in name:
                return sector_name
    return "其他"

def extract_exit_date(note):
    """从note中提取退出日期"""
    m = re.search(r'(20\d{2}-\d{2}-\d{2})', note)
    if m:
        return m.group(1)
    m2 = re.search(r'(\d{2})-(\d{2})清仓', note)
    if m2:
        return f'2026-{m2.group(1)}-{m2.group(2)}'
    return None

def compute_days_since_exit(note):
    """计算距退出日期的天数"""
    exit_date = extract_exit_date(note)
    if not exit_date:
        return 999
    try:
        exit_dt = datetime.strptime(exit_date, '%Y-%m-%d')
        return (datetime.now() - exit_dt).days
    except:
        return 999


# ============================================================
# 核心函数
# ============================================================

def compute_l6_hot(l5_stocks, etf_signals):
    """
    L6热点异动分析
    """
    engine_data = load_engine_signals()
    hot_monitor = load_hot_monitor()
    today = datetime.now().strftime("%Y-%m-%d")

    # 识别当前热门板块
    hot_sectors = set()
    for e in etf_signals:
        if abs(e['change']) > 2:
            for sname, sinfo in SECTOR_MAP.items():
                if e['name'] in sinfo.get('etfs', []):
                    hot_sectors.add(sname)

    hot_alerts = []

    for s in l5_stocks:
        chg = abs(s['change'])
        if chg < 5:
            continue

        code = s['code']
        name = s['name']
        sig = engine_data.get(code, {})
        sector = get_sector(code, name)

        total_score = float(sig.get('total_score_ext', 0))
        quality_score = float(sig.get('quality_score', 0))
        morph_score = float(sig.get('morph_score', 0))
        level = sig.get('price_level', 'L0_NORMAL')
        resonance = sig.get('resonance', {})
        verdict = resonance.get('verdict', '未知')
        signals = sig.get('signals', [])

        is_hot_sector = sector in hot_sectors
        sector_bonus = 1.0 if is_hot_sector else 0.0

        if total_score == 0 and not sig:
            total_score = min(chg / 3, 3.5)

        chg_score = min(chg / 10, 1.0) * 3
        p0_score = min(total_score / 5, 1.0) * 4
        sector_score = sector_bonus * 3
        composite = round(chg_score + p0_score + sector_score, 1)

        key_signals = []
        for sig_item in signals[:5]:
            rule = sig_item.get('rule', '')
            note = sig_item.get('note', '')
            short_note = note[:60] if note else rule
            key_signals.append(short_note)
        if not key_signals:
            key_signals.append(f"{s['direction']}{chg:.1f}%异动·{sector}")

        # 推荐逻辑
        direction = s['direction']
        is_buyable = False
        reason = ""
        is_crash_watch = False

        if direction == "🔴" and chg > 9:
            is_buyable = False
            reason = "涨停封板，等开板回踩"
        elif direction == "🔴" and total_score >= 2.0:
            is_buyable = True
            reason = f"放量强势+P0评分{total_score}，关注次日分歧低吸"
        elif direction == "🔴" and total_score < 2.0:
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
            if chg > 8:
                is_buyable = False
                is_crash_watch = True
                reason = f"大跌{chg:.1f}%→观察候补，等止跌K线确认后再评估"
            elif total_score >= 1.5 and '卖出' not in verdict:
                is_buyable = True
                reason = f"跌幅适中+P0评分{total_score}，关注左侧机会"
            else:
                is_buyable = False
                reason = "跌幅大无支撑信号，观望"

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
            "is_crash_watch": is_crash_watch,
            "reason": reason,
            "key_signals": key_signals,
            # 升级规则所需字段
            "sector_heat": sector_bonus * 3,
            "volume_ratio": s.get('volume_ratio', 0),
        }
        hot_alerts.append(alert)

        if code not in hot_monitor["stocks"]:
            hot_monitor["stocks"][code] = {
                "name": name,
                "first_seen": today,
                "sector": sector,
                "total_score": total_score,
            }

    # ── 降级逻辑（hot_monitor内部） ──
    today_active = {a['code'] for a in hot_alerts}
    stale = []
    for code, info in list(hot_monitor["stocks"].items()):
        if code not in today_active:
            inactive_days = info.get('inactive_days', 0) + 1
            info['inactive_days'] = inactive_days
            if info.get('total_score', 0) == 0.0 and inactive_days >= 3:
                stale.append(code)
            elif inactive_days >= 3:
                stale.append(code)
        else:
            info['inactive_days'] = 0
            for a in hot_alerts:
                if a['code'] == code:
                    info['total_score'] = a['p0_total']
                    break
    for code in stale:
        del hot_monitor["stocks"][code]

    hot_alerts.sort(key=lambda x: x['composite_score'], reverse=True)
    buyable = [a for a in hot_alerts if a['is_buyable']]

    hot_monitor["last_update"] = today

    result = {
        "hot_alerts": hot_alerts,
        "buyable_alerts": buyable,
        "hot_sectors": list(hot_sectors),
        "total_hot": len(hot_alerts),
        "total_buyable": len(buyable),
    }

    save_hot_monitor(hot_monitor)

    # 写入L6_hot_alerts.md
    with open(f"{ALERT_DIR}/L6_hot_alerts.md", 'w') as f:
        if not hot_alerts:
            f.write("今日无±5%以上异动\n")
        else:
            if hot_sectors:
                f.write(f"🔥 今日热门板块: {'/'.join(hot_sectors)}\n\n")
            if buyable:
                f.write("【可关注】\n")
                for a in buyable:
                    f.write(f"{a['direction']}{a['name']}({a['code']}) {a['price']}元 ({a['change']:+.2f}%) | 综合评分{a['composite_score']} P0总分{a['p0_total']} | {a['reason']}\n")
                    for sig in a['key_signals'][:3]:
                        f.write(f"   → {sig}\n")
                    f.write("\n")
            f.write("【异常波动·暂不参与】\n")
            for a in hot_alerts:
                if a['is_buyable']:
                    continue
                f.write(f"{a['direction']}{a['name']}({a['code']}) {a['price']}元 ({a['change']:+.2f}%) | P0{a['p0_total']} {a['sector']} | {a['reason']}\n")
                if a['key_signals']:
                    f.write(f"  {a['key_signals'][0]}\n")

    # ── 同步到 focus_watchlist.json（升级+降级）──
    sync_result = sync_to_focus_watchlist(buyable, hot_monitor, hot_alerts)

    # ── 生成每日升降级通报 ──
    generate_daily_report(result, sync_result, hot_monitor)

    return result


def sync_to_focus_watchlist(buyable_alerts, hot_monitor=None, hot_alerts=None):
    """
    将L6可参与热点标的同步到 focus_watchlist.json
    使用 should_upgrade_to_focus() 和 should_demote_from_focus() 规则引擎
    """
    ws = os.environ.get('WORKSPACE', '/root/.openclaw/workspace')
    focus_path = f"{ws}/stock-signals/focus_watchlist.json"
    if not os.path.exists(focus_path):
        return {"added": 0, "removed": 0, "total": 0, "crash_watch": []}

    try:
        with open(focus_path) as f:
            focus = json.load(f)
    except:
        return {"added": 0, "removed": 0, "total": 0, "crash_watch": []}

    focus_list = focus.get('focus_list', [])
    existing_codes = {s['code'] for s in focus_list}
    today = datetime.now().strftime("%Y-%m-%d")

    # 热点池信息
    hot_inactive = {}
    hot_scores = {}
    hot_codes = set()
    if hot_monitor:
        for code, info in hot_monitor.get('stocks', {}).items():
            hot_inactive[code] = info.get('inactive_days', 0)
            hot_scores[code] = info.get('total_score', 0)
            hot_codes.add(code)

    # ==================== 升级 ====================
    added = 0
    crash_watch = []
    upgrade_count_today = 0
    MAX_UPGRADE_PER_DAY = 3

    # 大跌观察
    if hot_alerts:
        for a in hot_alerts:
            if a.get('is_crash_watch') and a['code'] not in existing_codes:
                crash_watch.append(a)

    for a in buyable_alerts:
        code = a['code']

        # 已在池中 → 更新评分
        if code in existing_codes:
            for s in focus_list:
                if s['code'] == code:
                    if 'catalyst' in s and s['catalyst']:
                        if 'L6热点' not in s['catalyst']:
                            s['catalyst'] += f" | L6热点评分{a['composite_score']}"
                    break
            continue

        # 用规则引擎判定
        candidate = {
            **a,
            'in_positions': any(s.get('hold') and s['code'] == code for s in focus_list),
            'in_focus_watchlist': code in existing_codes,
        }
        should_up, reason = should_upgrade_to_focus(candidate)

        if not should_up:
            continue

        if upgrade_count_today >= MAX_UPGRADE_PER_DAY:
            break

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
            "note": f"{today} L6热点自动升级，评分{a['composite_score']}",
            "trial_period": True,
            "trial_days": 3,
            "upgrade_date": today,
            "upgrade_type": candidate.get('upgrade_type', 'normal'),
            "composite_score": a['composite_score'],
        })
        existing_codes.add(code)
        added += 1
        upgrade_count_today += 1

    # ==================== 降级 ====================
    removed = 0
    focus_list_new = []

    for s in focus_list:
        code = s['code']
        status = s.get('status', '')

        # 规则1：持仓保护
        if s.get('hold'):
            focus_list_new.append(s)
            continue

        days_since_exit = compute_days_since_exit(s.get('note', ''))
        current_score = hot_scores.get(code, s.get('composite_score', 0))
        inactive_days = hot_inactive.get(code, 0)

        # 构建降级判定所需的item
        item = {
            **s,
            'in_hot_monitor': code in hot_codes,
            'inactive_days': inactive_days,
            'consecutive_stop_loss_breaks': s.get('consecutive_stop_loss_breaks', 0),
            'avg_volume': s.get('avg_volume', 0),
        }

        # 评分连续低天数（从hot_monitor追踪）
        if current_score < 2.5:
            if 'low_score_days' not in s:
                s['low_score_days'] = 0
            s['low_score_days'] += 1
        else:
            s['low_score_days'] = 0
        consecutive_low_score_days = s.get('low_score_days', 0)

        should_demote, reason, target_layer = should_demote_from_focus(
            item, days_since_exit, current_score, consecutive_low_score_days
        )

        if should_demote:
            record_demotion(s, reason, s.get('stop_loss', 0), target_layer)
            removed += 1
            continue

        focus_list_new.append(s)

    focus['focus_list'] = focus_list_new
    focus['last_update'] = today

    with open(focus_path, 'w') as f:
        json.dump(focus, f, ensure_ascii=False, indent=2)

    if added or removed:
        log_msg = f"[L6升降级] 升级+{added} | 降级-{removed} | 池内{len(focus_list_new)}只"
        print(log_msg)
        log_path = f"{ALERT_DIR}/l6_upgrade_log.txt"
        with open(log_path, 'a') as f:
            f.write(f"{today} {log_msg}\n")

    return {"added": added, "removed": removed, "total": len(focus_list_new), "crash_watch": crash_watch}


def generate_daily_report(l6_result, sync_result, hot_monitor):
    """生成每日升降级通报（表格化+差异推送）"""
    today = datetime.now().strftime("%Y-%m-%d")
    report_path = f"{ALERT_DIR}/L6_daily_report.md"

    lines = []
    lines.append(f"# L6热点升降级日报 | {today}")
    lines.append("")

    # 热门板块
    if l6_result['hot_sectors']:
        lines.append(f"🔥 今日热门板块: {' / '.join(l6_result['hot_sectors'])}")
        lines.append("")

    # ⬆️ 升级
    if sync_result['added'] > 0:
        lines.append(f"## ⬆️ 升级（+{sync_result['added']}只）")
        lines.append("")
        lines.append("| 标的 | 涨跌 | 评分 | 板块 | 类型 | 备注 |")
        lines.append("|:-----|:----:|:----:|:-----|:----:|:-----|")
        for a in l6_result.get('buyable_alerts', []):
            if a['composite_score'] >= 4.0:
                utype = a.get('upgrade_type', 'normal')
                utype_label = "试用期" if a.get('trial_period') else "正式"
                lines.append(f"| {a['name']}({a['code']}) | {a['change']:+.2f}% | {a['composite_score']} | {a['sector']} | {utype_label} | {a['reason'][:30]} |")
        lines.append("")

    # ⬇️ 降级
    if sync_result['removed'] > 0:
        lines.append(f"## ⬇️ 降级（-{sync_result['removed']}只）")
        lines.append("")
        lines.append("| 标的 | 原因 | 降级至 | 降级时价格 |")
        lines.append("|:-----|:----:|:-------:|:----------:|")
        # 从demoted_history读取今日降级记录
        history_path = f"{ALERT_DIR}/demoted_history.json"
        if os.path.exists(history_path):
            try:
                with open(history_path) as f:
                    history = json.load(f)
                for h in history:
                    if h.get('demoted_date') == today:
                        lines.append(f"| {h['name']}({h['code']}) | {h['demoted_reason']} | {h['target_layer']} | {h['demoted_price']} |")
            except:
                pass
        lines.append("")

    # ⚠️ 需人工确认
    crash_watch = sync_result.get('crash_watch', [])
    if crash_watch:
        lines.append(f"## ⚠️ 需人工确认（{len(crash_watch)}只）")
        for a in crash_watch:
            lines.append(f"- **{a['name']}**({a['code']})：大跌{abs(a['change']):.1f}%，转入大跌观察池")
            lines.append(f"  → 条件：连续2日不创新低+止跌K线→允许升级")
        lines.append("")

    # 📊 池内统计
    net = sync_result['added'] - sync_result['removed']
    lines.append(f"## 📊 重点关注池统计")
    lines.append(f"- 当前池内：{sync_result['total']}只（净{'+' if net >= 0 else ''}{net}）")
    lines.append(f"- 今日升级：+{sync_result['added']}只")
    lines.append(f"- 今日降级：-{sync_result['removed']}只")

    # 试用期统计
    ws = os.environ.get('WORKSPACE', '/root/.openclaw/workspace')
    focus_path = f"{ws}/stock-signals/focus_watchlist.json"
    trial_count = 0
    if os.path.exists(focus_path):
        try:
            with open(focus_path) as f:
                focus = json.load(f)
            trial_count = sum(1 for s in focus.get('focus_list', []) if s.get('trial_period'))
        except:
            pass
    if trial_count > 0:
        lines.append(f"- 试用期中：{trial_count}只")
    lines.append("")

    # ⚡ 即将降级预警
    stocks = hot_monitor.get('stocks', {})
    warning_stocks = {c: i for c, i in stocks.items() if i.get('inactive_days', 0) >= 2}
    if warning_stocks:
        lines.append(f"## ⚡ 即将降级预警")
        for code, info in sorted(warning_stocks.items()):
            name = info.get('name', code)
            inactive = info.get('inactive_days', 0)
            score = info.get('total_score', 0)
            lines.append(f"- {name}({code}) 评分{score:.1f} 非活跃{inactive}天 → 再{3-inactive}天降级")
        lines.append("")

    with open(report_path, 'w') as f:
        f.write('\n'.join(lines))

    print(f"[L6日报] 已写入 {report_path}")


# 独立运行测试
if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(__file__))

    with open(f"{ALERT_DIR}/all_signals.json") as f:
        all_data = json.load(f)

    result = compute_l6_hot(all_data['L5_watchlist'], all_data['L4_etf_concept']['etf'])
    print(f"热点异动: {result['total_hot']}只 | 可参与: {result['total_buyable']}只")
    print(f"热门板块: {result['hot_sectors']}")
    for a in result['buyable_alerts'][:5]:
        print(f"  {a['direction']}{a['name']} +{a['change']:+.2f}% P0{a['p0_total']} 评分{a['composite_score']} → {a['reason'][:40]}")
    print(f"\n输出已写入 L6_hot_alerts.md")
