#!/usr/bin/env python3
"""模拟交易引擎 V2.0 — 集成信号引擎V3.0 + T+1约束"""
import json, urllib.request, subprocess, os, sys
from datetime import datetime

WORKSPACE = "/root/.openclaw/workspace/sim_trading"
ACCOUNT_FILE = f"{WORKSPACE}/account.json"
ENGINE = "/root/.openclaw/workspace/stock-signals/engine.sh"
SIGNAL_DIR = "/root/.openclaw/workspace/stock-signals"

def load_account():
    with open(ACCOUNT_FILE) as f:
        return json.load(f)

def save_account(acc):
    with open(ACCOUNT_FILE, 'w') as f:
        json.dump(acc, f, indent=2, ensure_ascii=False)

def get_signal_data():
    """从统一信号源读取最新信号（由engine.sh每10分钟生成到/tmp/stock_alerts/engine_signals.json）"""
    signal_file = "/tmp/stock_alerts/engine_signals.json"
    if os.path.exists(signal_file) and os.path.getsize(signal_file) > 10:
        with open(signal_file) as f:
            try:
                return json.load(f)
            except:
                pass
    
    # 如果统一文件不存在或无效，调用引擎生成一份（回退方案）
    print("统一信号文件不存在或无效，独立调用信号引擎...")
    try:
        result = subprocess.run(["bash", ENGINE], capture_output=True, text=True, timeout=60)
        for line in result.stdout.split('\n'):
            if line.startswith('signal_file='):
                sig_file = line.split('=')[1]
                if os.path.exists(sig_file):
                    with open(sig_file) as f:
                        data = json.load(f)
                    return data
        return []
    except Exception as e:
        print(f"信号引擎失败: {e}")
        return []

def calc_fees(amount, is_sell=False):
    commission = max(amount * 0.0005, 5.0)
    stamp = amount * 0.001 if is_sell else 0
    transfer = amount * 0.00002
    return commission + stamp + transfer

def market_blocked(signals):
    """检查市场过滤器是否激活"""
    for s in signals:
        if s.get('code') == '000001':
            chg = float(s.get('change_pct', '0').replace('%', ''))
            if chg < -1.0:
                return True, f"上证{chg:+.2f}%"
            return False, ""
    return False, "无指数数据"

def get_market_change(signals):
    """获取上证指数涨跌幅"""
    for s in signals:
        if s.get('code') == '000001':
            return float(s.get('change_pct', '0').replace('%', ''))
    return 0.0

def get_sector_benchmark(signals, code):
    """获取标的所属板块的基准涨跌幅
    
    通过标的前缀判断版块:
    - 600/601/603: 使用芯片ETF作为科技板块基准
    - 000/002: 使用有色ETF作为资源板块基准
    - 300/301: 使用创新药ETF作为成长板块基准
    - 688: 使用芯片ETF
    """
    sector_map = {
        'sh516640': ('600', '601', '603', '688'),  # 芯片ETF
        'sz159667': ('300', '301'),                  # 工业母机ETF (成长)
        'sh512400': ('000', '002'),                  # 有色ETF (资源)
    }
    
    for etf_id, prefixes in sector_map.items():
        if any(code.startswith(p) for p in prefixes):
            for s in signals:
                if s.get('code') == etf_id.replace('sh','').replace('sz',''):
                    chg = s.get('change_pct', '0')
                    return float(chg.replace('%', ''))
                # 也尝试完整代码匹配
                sid = s.get('code', '')
                if sid == etf_id.replace('sh','').replace('sz',''):
                    chg = s.get('change_pct', '0')
                    return float(chg.replace('%', ''))
    return 0.0

def check_sector_strength(signals, code):
    """检查板块强度: 板块ETF涨跌幅≥0.5% 或 同板块≥2只标的涨>2%"""
    # 方式1: ETF基准
    etf_chg = get_sector_benchmark(signals, code)
    if etf_chg >= 0.5:
        return True
    
    # 方式2: 同板块个股联动
    # 根据code前缀分组
    prefix = code[:3] if len(code) >= 3 else code[0]
    same_sector_strong = 0
    for s in signals:
        sc = s.get('code', '')
        if sc == code or sc in ['000001', '399001', '399006', '000688']:
            continue
        if sc.startswith(prefix):
            chg = float(s.get('change_pct', '0').replace('%', ''))
            if chg > 2.0:
                same_sector_strong += 1
    
    return same_sector_strong >= 2

def is_in_cooldown(acc, code):
    """检查是否处于信号冷却期（同标的止损后3日内禁止回购）"""
    today = datetime.now().strftime('%Y-%m-%d')
    today_dt = datetime.strptime(today, '%Y-%m-%d')
    
    # 查找最近一次该标的的止损卖出
    for t in reversed(acc.get('trades', [])):
        if t.get('type') == 'SELL' and t.get('code') == code:
            reason = t.get('reason', '')
            if '止损' in reason or '信号引擎卖出' in reason:
                sell_date = t.get('time', '')[:10]
                sell_dt = datetime.strptime(sell_date, '%Y-%m-%d')
                days_diff = (today_dt - sell_dt).days
                if days_diff <= 3:
                    return True  # 3日内止损过，冷却中
                break  # 只检查最后一次
    return False

def get_resonance(signals, code):
    """从信号引擎获取共振判决"""
    for s in signals:
        if s.get('code') == code:
            r = s.get('resonance', {})
            return r.get('verdict', '观望'), r.get('buy_signals', 0), r.get('sell_signals', 0)
    return '无数据', 0, 0

def get_stop_from_signal(signals, code, cost):
    """从信号规则中提取止损价——技术位优先，买入价为最终兜底
    
    止损逻辑优先级（V2.1优化）:
    1. 信号引擎的 entry_stop_loss（阳线50%位/支撑位）
    2. MA20下方3%（趋势破位线）
    3. 20日最低价下方1%（前低支撑）
    4. 成本下方8%（宽幅兜底，避免正常波动扫损）
    """
    best_stop = cost * 0.92  # 兜底：成本下方8%
    
    for s in signals:
        if s.get('code') != code:
            continue
        
        # 1. 信号引擎的止损位（阳线50%/关键支撑）
        for sig in s.get('signals', []):
            rule = sig.get('rule', '')
            if rule == 'entry_stop_loss':
                sp = sig.get('body_50pct')
                if sp:
                    sig_stop = float(sp)
                    # 止损位不能离买入价太近（<2%），否则用更宽松的技术位
                    if sig_stop < cost * 0.98:
                        best_stop = max(best_stop, sig_stop)
        
        # 2. MA20下方3%（趋势破位线）
        ma20 = s.get('ma20', 'n/a')
        if ma20 != 'n/a':
            try:
                ma20_stop = float(ma20) * 0.97
                if ma20_stop < cost:  # 只在亏损方向设止损
                    best_stop = max(best_stop, ma20_stop)
            except:
                pass
        
        # 3. 20日最低价下方1%（前低支撑）
        # 从形态信号中提取低点信息
        for sig in s.get('signals', []):
            low20 = sig.get('low20')
            if low20:
                try:
                    low_stop = float(low20) * 0.99
                    if low_stop < cost:
                        best_stop = max(best_stop, low_stop)
                except:
                    pass
    
    # 保证止损位至少在成本下方3%（避免买入即止损）
    min_stop = cost * 0.97
    if best_stop > min_stop:
        best_stop = min_stop
    
    return round(best_stop, 2)

def is_t1_locked(holding):
    """检查是否T+1锁定"""
    buy_date = holding.get('last_buy_date', '')
    today = datetime.now().strftime('%Y-%m-%d')
    return buy_date == today

def execute_buy(acc, code, name, price, qty, reason, signals):
    """执行买入"""
    amount = price * qty
    fees = calc_fees(amount, False)
    total_cost = amount + fees

    if acc["cash"] < total_cost:
        return False, f"现金不足"

    max_pos = acc["initial_capital"] * acc["max_position_per_stock"]
    existing = acc["holdings"].get(code, {})
    existing_val = existing.get("qty", 0) * existing.get("avg_cost", 0)
    if existing_val + amount > max_pos:
        return False, f"单只超限"

    total_stock = sum(h["qty"] * h["avg_cost"] for h in acc["holdings"].values())
    if total_stock + amount > acc["initial_capital"] * acc["max_total_position"]:
        return False, f"总仓位超限"

    today = datetime.now().strftime("%Y-%m-%d")
    if code in acc["holdings"]:
        old = acc["holdings"][code]
        new_qty = old["qty"] + qty
        new_cost = (old["qty"] * old["avg_cost"] + amount) / new_qty
        acc["holdings"][code] = {"name": name, "qty": new_qty, "avg_cost": round(new_cost, 3),
                                 "last_buy_price": price, "last_buy_date": today}
    else:
        acc["holdings"][code] = {"name": name, "qty": qty, "avg_cost": round(price, 3),
                                 "last_buy_price": price, "last_buy_date": today}

    acc["cash"] -= total_cost
    acc["trades"].append({
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "type": "BUY",
        "code": code, "name": name, "price": price, "qty": qty,
        "amount": amount, "fees": round(fees, 2), "reason": reason
    })
    return True, None

def execute_sell(acc, code, name, price, qty, reason):
    """执行卖出"""
    if code not in acc["holdings"]:
        return False, f"无持仓: {name}"
    h = acc["holdings"][code]
    if is_t1_locked(h):
        return False, f"T+1锁定: {name}"
    if qty > h["qty"]:
        qty = h["qty"]

    amount = price * qty
    fees = calc_fees(amount, True)
    cost = qty * h["avg_cost"]
    profit = amount - cost - fees

    h["qty"] -= qty
    if h["qty"] <= 0:
        del acc["holdings"][code]

    acc["cash"] += amount - fees
    acc["trades"].append({
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "type": "SELL",
        "code": code, "name": name, "price": price, "qty": qty,
        "amount": amount, "fees": round(fees, 2),
        "profit": round(profit, 2), "profit_pct": round(profit/cost*100, 2) if cost else 0,
        "reason": reason
    })
    return True, None

def save_signals_for_push(signals, executed, blocked, mkt_note):
    """将信号引擎判决写入持久文件，用于AI推送"""
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().strftime("%H:%M:%S")
    data_dir = f"{WORKSPACE}/data"
    os.makedirs(data_dir, exist_ok=True)

    lines = []
    lines.append(f"## {today} {now} 信号扫描\n")
    if blocked:
        lines.append(f"⏸️ 市场过滤器激活: {mkt_note}\n")
    else:
        lines.append(f"✅ 市场环境正常\n")

    # 从L3焦点池+全池中找有判决的标的（统一评分标准）
    focus_codes = get_focus_codes(signals)
    actionable = []
    for s in signals:
        code = s.get('code', '')
        if code in ['000001', '399001', '399006', '000688']: continue
        name = s.get('name', '?')
        price = s.get('price', 0)
        change = s.get('change_pct', '0')
        r = s.get('resonance', {})
        verdict = r.get('verdict', '观望')
        buy_cnt = r.get('buy_signals', 0)
        sell_cnt = r.get('sell_signals', 0)
        p0_total = float(s.get('total_score_ext', 0))
        ma5 = s.get('ma5', 'n/a')
        ma10 = s.get('ma10', 'n/a')
        level = s.get('price_level', 'L0_NORMAL')
        
        # === 统一选股条件 (V2.1) ===
        # 买入: 三重共振直接通过 | 双重确认+P0≥3.5 | 双重确认+P0≥2.5需板块验证
        # 卖出: 卖出信号≥2
        from_buy = verdict == '三重共振-出手' or (verdict == '双重确认-可参与' and p0_total >= 3.5)
        from_buy_weak = verdict == '双重确认-可参与' and p0_total >= 2.5 and p0_total < 3.5
        is_sell = sell_cnt >= 2
        
        if from_buy or is_sell or from_buy_weak:
            actionable.append({
                'code': code, 'name': name, 'price': price,
                'change': change, 'verdict': verdict,
                'buy_cnt': buy_cnt, 'sell_cnt': sell_cnt,
                'p0_total': p0_total, 'level': level,
                'ma5': ma5, 'ma10': ma10,
                'in_focus': code in focus_codes
            })

    if actionable:
        lines.append(f"\n### 买入信号\n")
        for a in actionable:
            if a['verdict'] in ['三重共振-出手', '双重确认-可参与']:
                focus_tag = '🎯焦点' if a['in_focus'] else ''
                lines.append(f"- **{a['name']}({a['code']})**: {a['verdict']} | P0={a['p0_total']} | 价{a['price']} | 涨幅{a['change']} | MA5={a['ma5']} | buy_signals={a['buy_cnt']} {focus_tag}")
        lines.append(f"\n### 卖出信号\n")
        for a in actionable:
            if a['sell_cnt'] >= 2:
                lines.append(f"- **{a['name']}({a['code']})**: sell_signals={a['sell_cnt']} | P0={a['p0_total']} | 价{a['price']} | 涨幅{a['change']}")
    else:
        lines.append(f"\n无行行动信号\n")

    lines.append(f"\n### 执行结果\n")
    if executed:
        for d in executed:
            lines.append(f"- {'买入' if d['action']=='BUY' else '卖出'} {d['name']}({d['code']}) {d.get('qty','?')}股@{d.get('price',0):.2f} | {d.get('reason','')}")
    else:
        lines.append(f"- 本次无交易执行\n")

    report = '\n'.join(lines)
    # 覆盖最新
    with open(f"{data_dir}/signals_push.md", 'w') as f:
        f.write(report)
    # 追加日报志
    with open(f"{data_dir}/signals_{today.replace('-','')}.md", 'a') as f:
        f.write(report + '\n\n')

    return report

def write_shared_status(acc, decisions, report):
    """写入共享状态文件，供实盘监控推送cron读取"""
    sim_status = "/tmp/stock_alerts/sim_status.txt"
    total_market_value = 0
    for code, h in acc['holdings'].items():
        total_market_value += h.get('qty', 0) * h.get('avg_cost', 0)
    
    lines = [f"账户净值: ¥{acc['total_value']:.2f} (初始¥{acc['initial_capital']:.0f})"]
    lines.append(f"现金: ¥{acc['cash']:.2f} | 持仓市值: ¥{total_market_value:.2f}")
    lines.append(f"总回报: +{(acc['total_value']-acc['initial_capital'])/acc['initial_capital']*100:.2f}%" if acc['total_value'] >= acc['initial_capital'] else f"总回报: {(acc['total_value']-acc['initial_capital'])/acc['initial_capital']*100:.2f}%")
    
    if decisions:
        lines.append(f"\n今日操作:")
        for d in decisions:
            action = '买入' if d.get('action') == 'BUY' else '卖出'
            lines.append(f"  {action} {d['name']}({d['code']}) {d['qty']}股@{d['price']:.2f} | {d['reason'][:50]}")
    
    lines.append(f"\n持仓: {len(acc['holdings'])}只标的")
    os.makedirs(os.path.dirname(sim_status), exist_ok=True)
    with open(sim_status, 'w') as f:
        f.write('\n'.join(lines))


def run_trading_session():
    acc = load_account()
    signals = get_signal_data()
    if not signals:
        print("⚠️ 无信号数据")
        return [], {"cash": acc["cash"], "stock_value": 0, "total_value": acc["total_value"], "holdings_count": 0, "total_return_pct": 0}

    # 市场过滤器
    blocked, mkt_note = market_blocked(signals)
    MARKET_CHG = get_market_change(signals)  # V2.1: 全局市场涨跌幅
    if blocked:
        print(f"⏸️ 市场过滤器激活: {mkt_note}")

    decisions = []
    
    # 获取当前持仓的MA5/MA10数据（用于移动止盈）
    def get_ma_data(signals, code):
        for s in signals:
            if s.get('code') == code:
                ma5 = s.get('ma5', 'n/a')
                ma10 = s.get('ma10', 'n/a')
                # ma5可能是复合值如"841.15"，尝试解析
                try:
                    return float(ma5) if ma5 != 'n/a' else None, float(ma10) if ma10 != 'n/a' else None
                except:
                    return None, None
        return None, None

    # 1. 持仓检查 (止损+移动止盈)
    for code, holding in acc["holdings"].items():
        verdict, buy_cnt, sell_cnt = get_resonance(signals, code)
        
        # 获取当前价
        cur_price = None
        for s in signals:
            if s.get('code') == code:
                cur_price = s.get('price', 0)
                break
        if not cur_price: continue

        loss_pct = (cur_price - holding["avg_cost"]) / holding["avg_cost"] * 100
        
        # === V2.1 移动止盈（盈利标的） ===
        if loss_pct > 5:
            ma5, ma10 = get_ma_data(signals, code)
            if ma5 and cur_price <= ma5:
                # 盈利>5%后跌破MA5 → 移动止盈
                decisions.append({
                    "action": "SELL", "code": code, "name": holding["name"],
                    "qty": holding["qty"], "price": cur_price,
                    "reason": f"移动止盈(MA5): 盈{loss_pct:+.1f}% 跌破MA5={ma5:.2f}"
                })
                continue
            if ma10 and cur_price <= ma10 and loss_pct > 10:
                # 盈利>10%后跌破MA10 → 趋势止盈
                decisions.append({
                    "action": "SELL", "code": code, "name": holding["name"],
                    "qty": holding["qty"], "price": cur_price,
                    "reason": f"移动止盈(MA10): 盈{loss_pct:+.1f}% 跌破MA10={ma10:.2f}"
                })
                continue
        
        # === 止损检查（使用技术位止损） ===
        stop_price = get_stop_from_signal(signals, code, holding["avg_cost"])

        # 止损触发
        if cur_price <= stop_price:
            decisions.append({
                "action": "SELL", "code": code, "name": holding["name"],
                "qty": holding["qty"], "price": cur_price,
                "reason": f"止损触发: {loss_pct:.1f}% (止损价{stop_price:.2f})"
            })
            continue

        # 该涨不涨 (大盘>0.3% 但个股跌>2%)
        for s in signals:
            if s.get('code') == '000001':
                mkt_ch = float(s.get('change_pct', '0').replace('%', ''))
                if mkt_ch > 0.3 and loss_pct < -2:
                    decisions.append({
                        "action": "SELL", "code": code, "name": holding["name"],
                        "qty": holding["qty"], "price": cur_price,
                        "reason": f"该涨不涨: 大盘{mkt_ch:+.2f}% 个股{loss_pct:+.1f}%"
                    })
                break

        # 信号引擎卖出确认
        if sell_cnt >= 2 and loss_pct < -3:
            if not any(d["code"] == code and d["action"] == "SELL" for d in decisions):
                decisions.append({
                    "action": "SELL", "code": code, "name": holding["name"],
                    "qty": holding["qty"], "price": cur_price,
                    "reason": f"信号引擎卖出确认(sell_signals={sell_cnt})"
                })

    # 2. 机会扫描 (仅在市场未阻塞且无止损待处理时)
    # V2.1 买入门槛升级: P0≥3.5 + 板块强度≥B + 环境过滤器 + 信号冷却期
    if not blocked and not any(d["action"] == "SELL" for d in decisions):
        # 大盘环境过滤器：上证跌>1.5%时禁止新开仓
        mkt_chg = MARKET_CHG  # 由外部传入的市场涨跌幅
        mkt_blocked_new = mkt_chg < -1.5
        if mkt_blocked_new:
            print(f"⏸️ 大盘环境过滤器激活: 上证{mkt_chg:+.2f}%，禁止新开仓")
            mkt_blocked_new = True
        
        if not mkt_blocked_new:
            for s in signals:
                code = s.get('code', '')
                # 跳过指数
                if code in ['000001', '399001', '399006', '000688']: continue
                # 跳过已持仓
                if code in acc["holdings"]: continue
                
                verdict, buy_cnt, sell_cnt = get_resonance(signals, code)
                p0_total = float(s.get('total_score_ext', 0))
                
                # === V2.1 买入门槛 ===
                # 三重共振直接通过（代表最高级别信号）
                if verdict == '三重共振-出手':
                    pass  # 通过
                elif verdict == '双重确认-可参与' and p0_total >= 3.5:
                    pass  # 双重确认+P0≥3.5 通过
                elif verdict == '双重确认-可参与' and p0_total >= 2.5:
                    # P0在2.5-3.5之间: 需要板块强度验证
                    sector_ok = check_sector_strength(signals, code)
                    if not sector_ok:
                        continue  # 板块不够强，跳过
                else:
                    continue  # 其他情况跳过
                
                # === 信号冷却期 ===
                if is_in_cooldown(acc, code):
                    continue  # 同标的止损后3日内不再买入

                name = s.get('name', '?')
                price = s.get('price', 0)
                change = float(s.get('change_pct', '0').replace('%', ''))
                ma5 = s.get('ma5', 'n/a')

                if change <= 0: continue  # 下跌不买
                if price <= 0: continue

                # 买入量 (100股整数倍)
                # 信号强度决定仓位: 三重共振=满单票上限, 双重确认=半仓
                max_pos_ratio = 0.2 if verdict == '三重共振-出手' else 0.1
                max_amount = min(acc["cash"] * 0.4, acc["initial_capital"] * max_pos_ratio)
                qty = int(max_amount / price / 100) * 100
                if qty < 100: continue

                decisions.append({
                    "action": "BUY", "code": code, "name": name,
                    "price": price, "qty": qty,
                    "reason": f"{verdict} | P0={p0_total:.1f} | 涨{change:+.2f}% | buy_signals={buy_cnt}"
                })

    # 3. 执行决策
    executed = []
    for d in decisions:
        if d["action"] == "BUY":
            ok, err = execute_buy(acc, d["code"], d["name"], d["price"], d["qty"], d["reason"], signals)
            if ok:
                print(f"✅ BUY {d['name']} {d['qty']}股@{d['price']:.2f}")
                executed.append(d)
            else:
                print(f"❌ BUY失败 {d['name']}: {err}")
        elif d["action"] == "SELL":
            ok, err = execute_sell(acc, d["code"], d["name"], d["price"], d["qty"], d["reason"])
            if ok:
                print(f"✅ SELL {d['name']} {d['qty']}股@{d['price']:.2f}")
                executed.append(d)
            else:
                print(f"⏸️ SELL跳过 {d['name']}: {err}")

    # 4. 更新市值
    stock_value = 0
    for code, h in acc["holdings"].items():
        for s in signals:
            if s.get('code') == code:
                stock_value += h["qty"] * s.get('price', 0)
                break
    acc["total_value"] = acc["cash"] + stock_value

    snapshot = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "cash": round(acc["cash"], 2), "stock_value": round(stock_value, 2),
        "total_value": round(acc["total_value"], 2),
        "total_return_pct": round((acc["total_value"] - acc["initial_capital"]) / acc["initial_capital"] * 100, 2),
        "holdings_count": len(acc["holdings"]), "market_blocked": blocked
    }
    # 保存信号报告供AI推送
    save_signals_for_push(signals, executed, blocked, mkt_note)

    acc["daily_snapshots"].append(snapshot)
    save_account(acc)
    return executed, snapshot

def daily_report():
    acc = load_account()
    today = datetime.now().strftime("%Y-%m-%d")
    r = []
    r.append(f"## 模拟交易日报 | {today}\n")
    r.append(f"**总资产:** ¥{acc['total_value']:.2f}")
    r.append(f"**收益率:** {(acc['total_value']-acc['initial_capital'])/acc['initial_capital']*100:+.2f}%")
    r.append(f"**现金:** ¥{acc['cash']:.2f}  **持仓:** {len(acc['holdings'])}只\n")
    for c, h in acc["holdings"].items():
        r.append(f"- {h['name']}({c}): {h['qty']}股 成本{h['avg_cost']:.2f}")
    trades = [t for t in acc["trades"] if t["time"].startswith(today)]
    if trades:
        r.append(f"\n### 今日交易 ({len(trades)}笔)\n")
        for t in trades:
            if t["type"] == "BUY":
                r.append(f"- 买入 {t['name']} {t['qty']}股@{t['price']:.2f}")
            else:
                r.append(f"- 卖出 {t['name']} {t['qty']}股@{t['price']:.2f} 盈亏{t.get('profit',0):+.2f}")
    report = "\n".join(r)
    os.makedirs(f"{WORKSPACE}/reports", exist_ok=True)
    with open(f"{WORKSPACE}/reports/daily_{today}.md", 'w') as f:
        f.write(report)
    return report

def get_focus_codes(signals):
    """从信号中提取L3焦点池标的"""
    focus = set()
    for s in signals:
        level = s.get('price_level', '')
        code = s.get('code', '')
        if level == 'L3_FOCUS' or level == 'P0_TOP':
            focus.add(code)
    return focus


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "report":
        print(daily_report())
    else:
        execs, snap = run_trading_session()
        print(f"\n💰 ¥{snap['total_value']:.2f} ({snap['total_return_pct']:+.2f}%) | 📦{snap['holdings_count']}只 | 💵¥{snap['cash']:.2f}")
