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

def get_resonance(signals, code):
    """从信号引擎获取共振判决"""
    for s in signals:
        if s.get('code') == code:
            r = s.get('resonance', {})
            return r.get('verdict', '观望'), r.get('buy_signals', 0), r.get('sell_signals', 0)
    return '无数据', 0, 0

def get_stop_from_signal(signals, code, cost):
    """从信号规则中提取止损价"""
    for s in signals:
        if s.get('code') != code: continue
        for sig in s.get('signals', []):
            rule = sig.get('rule', '')
            if rule == 'entry_stop_loss':
                sp = sig.get('body_50pct')
                if sp: return float(sp)
        # fallback: MA5下方3%
        ma5 = s.get('ma5', 'n/a')
        if ma5 != 'n/a':
            return float(ma5) * 0.97
    return cost * 0.95  # 最终fallback

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
        
        # === 统一选股条件 ===
        # 买入: L3焦点池或P0≥2.0 + 共振判决=出手/可参与
        # 卖出: 卖出信号≥2
        is_buy = (verdict in ['三重共振-出手', '双重确认-可参与']) and (code in focus_codes or p0_total >= 2.0)
        is_sell = sell_cnt >= 2
        
        if is_buy or is_sell:
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
    if blocked:
        print(f"⏸️ 市场过滤器激活: {mkt_note}")

    decisions = []

    # 1. 止损检查 (使用信号引擎的止损价)
    for code, holding in acc["holdings"].items():
        verdict, buy_cnt, sell_cnt = get_resonance(signals, code)
        stop_price = get_stop_from_signal(signals, code, holding["avg_cost"])
        # 获取当前价
        cur_price = None
        for s in signals:
            if s.get('code') == code:
                cur_price = s.get('price', 0)
                break
        if not cur_price: continue

        loss_pct = (cur_price - holding["avg_cost"]) / holding["avg_cost"] * 100

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
    if not blocked and not any(d["action"] == "SELL" for d in decisions):
        for s in signals:
            code = s.get('code', '')
            # 跳过指数和非标的
            if code in ['000001', '399001', '399006']: continue
            # 跳过已持仓
            if code in acc["holdings"]: continue
            # 跳过历史自选池里的（只看持仓信号）
            if code not in [h for h in acc['holdings'].keys()]:
                # 非持仓标的：只看共振买入
                verdict, buy_cnt, _ = get_resonance(signals, code)
                if verdict not in ['三重共振-出手', '双重确认-可参与']: continue

            name = s.get('name', '?')
            price = s.get('price', 0)
            change = float(s.get('change_pct', '0').replace('%', ''))
            ma5 = s.get('ma5', 'n/a')

            if change <= 0: continue  # 下跌不买
            if price <= 0: continue

            # 买入量 (100股整数倍)
            max_amount = min(acc["cash"] * 0.4, acc["initial_capital"] * 0.2)
            qty = int(max_amount / price / 100) * 100
            if qty < 100: continue

            verdict, buy_cnt, _ = get_resonance(signals, code)
            decisions.append({
                "action": "BUY", "code": code, "name": name,
                "price": price, "qty": qty,
                "reason": f"{verdict} | 涨{change:+.2f}% | buy_signals={buy_cnt}"
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
