#!/usr/bin/env python3
"""
投资组合管理引擎 (Portfolio Engine)
===================================
职责：连接量化系统与项目目标，提供目标驱动的组合决策支持。

输入：
  1. data/investment_goals.json  — 用户可编辑的目标配置
  2. /tmp/stock_alerts/engine_signals.json — 信号引擎输出
  3. TOOLS.md — 持仓/成本/买卖记录（由 parse_tools.py 解析）

输出：
  data/portfolio_snapshot.json   — 每日净值快照
  data/portfolio_history.json    — 净值时间序列
  data/milestone_status.json     — 里程碑进度
  stdout                        — 可读摘要，供 cron 直接推送

架构原则：
  - 所有阈值、目标值从 goals 配置读取，不硬编码
  - 持仓从 TOOLS.md 解析，真实持仓和模拟交易隔离
  - 可用现金 = 截图基准 + 今日清仓回笼 - 今日建仓支出（动态计算）
  - 可用作 cron standalone 脚本或 import 模块
"""

import json
import os
import sys
import re
import time
from datetime import datetime, date
from pathlib import Path

# ======================== 路径配置 ========================
WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace"))
GOALS_FILE = WORKSPACE / "data" / "investment_goals.json"
SNAPSHOT_FILE = WORKSPACE / "data" / "portfolio_snapshot.json"
HISTORY_FILE = WORKSPACE / "data" / "portfolio_history.json"
MILESTONE_FILE = WORKSPACE / "data" / "milestone_status.json"
TOOLS_FILE = WORKSPACE / "TOOLS.md"
SIGNALS_FILE = Path("/tmp/stock_alerts/engine_signals.json")
TZ = "Asia/Shanghai"


def today():
    return datetime.now().strftime("%Y-%m-%d")


def now_iso():
    return datetime.now().isoformat()


# ======================== 行情读取 ========================

def _read_live_prices():
    """从 engine_signals.json 读实时价格。"""
    if not SIGNALS_FILE.exists():
        return {}
    try:
        with open(SIGNALS_FILE) as f:
            raw = json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}
    prices = {}
    for item in raw if isinstance(raw, list) else []:
        code = item.get("code", "")
        price = max(filter(None, [
            item.get("price"), item.get("close"), item.get("last"),
        ]), default=None)
        name = item.get("name", "")
        if code and price is not None:
            prices[code] = {"price": float(price), "name": name}
    return prices


# ======================== TOOLS.md 解析 ========================

def _parse_real_positions():
    """从 TOOLS.md 解析辉哥真实持仓。
    返回: list[{code, name?, shares, cost, avg_cost?}]
    """
    if not TOOLS_FILE.exists():
        return []
    try:
        text = TOOLS_FILE.read_text(encoding="utf-8")
    except Exception:
        return []

    positions = []
    in_section = False
    for line in text.splitlines():
        line = line.strip()

        if re.match(r'^###\s+持仓', line):
            in_section = True
            continue

        if in_section and line.startswith('###') and '持仓' not in line:
            break
        if in_section and line.startswith('##') and '持仓' not in line:
            break

        if not in_section:
            continue

        m = re.match(r'-\s*(.+?)\s+(\d{6})\D+(\d+)\s*股.*?成本\s*([\d.]+)', line)
        if not m:
            continue
        name = m.group(1).strip()
        code = m.group(2)
        shares = int(m.group(3))
        cost = float(m.group(4))

        if '清仓' in line and ('【' in line or '】' in line):
            if re.search(r'(误报清仓|截图纠正|实际未清仓)', line):
                pass
            elif re.search(r'加仓', line) and not re.search(r'清仓@', line):
                pass
            else:
                continue

        positions.append({
            "code": code,
            "name": name,
            "shares": shares,
            "cost": cost,
            "avg_cost": cost,
        })

    return positions


def _parse_cash_base():
    """从 TOOLS.md 截图可用资金行解析现金基准及截图日期。
    返回: (金额, 截图日期字符串) 或 (None, None)"""
    if not TOOLS_FILE.exists():
        return None, None
    try:
        text = TOOLS_FILE.read_text(encoding='utf-8')
        for line in text.splitlines():
            if '截图可用资金' in line or '可用资金' in line:
                m = re.search(r'(\d{1,3}(?:,\d{3})*(?:\.\d+)?)', line)
                m_date = re.search(r'(\d{4}-\d{2}-\d{2})', line)
                if m:
                    amount = float(m.group(1).replace(',', ''))
                    snap_date = m_date.group(1) if m_date else None
                    return amount, snap_date
    except Exception:
        pass
    return None, None


def _parse_trades_since(since_date):
    """从 TOOLS.md 解析 since_date（含）之后的所有买卖记录。
    返回: (清仓回笼总额, 建仓支出总额)
    """
    if not TOOLS_FILE.exists():
        return 0.0, 0.0
    try:
        text = TOOLS_FILE.read_text(encoding='utf-8')
    except Exception:
        return 0.0, 0.0

    sell_total = 0.0
    buy_total = 0.0

    # ===== 解析清仓记录（所有日期 >= since_date 的节）=====
    in_sell_section = False
    current_section_date = None
    for line in text.splitlines():
        stripped = line.strip()

        m_section = re.match(r'^###\s+今日清仓记录.*（(.+?)）', stripped)
        if m_section:
            current_section_date = m_section.group(1)
            in_sell_section = (current_section_date >= since_date)
            continue

        if in_sell_section and (stripped.startswith('###') or stripped.startswith('##')):
            break
        if not in_sell_section:
            continue

        # 分批格式: "其中200@22.40+200@22.90+400@22.98"
        batch_total = 0.0
        batch_parts = re.findall(r'(\d+)\s*@\s*([\d.]+)', stripped)
        for shares_str, price_str in batch_parts:
            batch_total += int(shares_str) * float(price_str)

        if batch_total > 0:
            sell_total += batch_total
        else:
            # 单次清仓: 清仓@XX.XX，亏约¥-XXX(-XX.X%)【...成本XX.XX】
            m_single = re.match(r'-\s*.+?\s+(\d{6})\s*[:：]?\s*清仓@([\d.]+)', stripped)
            if m_single:
                code = m_single.group(1)
                price = float(m_single.group(2))
                m_loss = re.search(r'亏约.*?(-?[\d,]+)', stripped)
                m_cost = re.search(r'成本([\d.]+)', stripped)
                loss_amount = None
                if m_loss:
                    loss_amount = abs(float(m_loss.group(1).replace(',', '')))
                if loss_amount and m_cost:
                    cost_price = float(m_cost.group(1))
                    if cost_price > price:
                        shares = round(loss_amount / (cost_price - price))
                        if shares > 0:
                            sell_total += shares * price
                if sell_total == 0:
                    shares = _find_position_shares(code)
                    if shares > 0:
                        sell_total += shares * price

    # ===== 解析建仓（持仓节中标注日期 >= since_date 的标的）=====
    in_pos_section = False
    for line in text.splitlines():
        stripped = line.strip()

        if re.match(r'^###\s+持仓', stripped):
            in_pos_section = True
            continue
        if in_pos_section and (stripped.startswith('###') or stripped.startswith('##')):
            break
        if not in_pos_section:
            continue

        m = re.match(r'-\s*(.+?)\s+(\d{6})\D+(\d+)\s*股.*?成本\s*([\d.]+).*?(\d{4}-\d{2}-\d{2})建仓', stripped)
        if m:
            shares = int(m.group(3))
            cost = float(m.group(4))
            buy_date = m.group(5)
            # 截图日期当天的建仓已在截图余额中体现，不重复扣除
            if buy_date >= since_date and buy_date != since_date:
                buy_total += shares * cost

    return sell_total, buy_total


def _find_position_shares(code):
    """从 TOOLS.md 持仓节查找某代码的股数"""
    if not TOOLS_FILE.exists():
        return 0
    try:
        text = TOOLS_FILE.read_text(encoding='utf-8')
    except Exception:
        return 0

    in_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r'^###\s+持仓', stripped):
            in_section = True
            continue
        if in_section and (stripped.startswith('###') or stripped.startswith('##')):
            break
        if not in_section:
            continue

        m = re.match(r'-\s*.+?\s+' + re.escape(code) + r'\D+(\d+)\s*股', stripped)
        if m:
            return int(m.group(1))
    return 0


# ======================== 核心计算 ========================

def load_goals():
    """加载投资目标配置"""
    if not GOALS_FILE.exists():
        return None
    with open(GOALS_FILE, encoding="utf-8") as f:
        return json.load(f)


def compute_net_worth(positions, live_prices):
    """计算当前账户净值：持仓市值 + 可用现金（动态计算）
    live_prices: {code: {price, name}}
    """
    total_market_value = 0.0
    details = []

    for pos in positions:
        code = pos["code"]
        price_info = live_prices.get(code, {})
        current_price = price_info.get("price") if price_info else None

        if current_price is None:
            current_price = pos["cost"]

        market_val = pos["shares"] * current_price
        pnl = market_val - pos["shares"] * pos["cost"]
        pnl_pct = (pnl / (pos["shares"] * pos["cost"])) * 100 if pos["cost"] > 0 else 0

        total_market_value += market_val
        details.append({
            "code": code,
            "name": pos["name"],
            "shares": pos["shares"],
            "cost": pos["cost"],
            "avg_cost": pos.get("avg_cost", pos["cost"]),
            "current_price": round(current_price, 2),
            "market_value": round(market_val, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
        })

    # ===== 现金动态计算 =====
    # 1. 截图基准（截图时的可用资金）
    cash_base, snap_date = _parse_cash_base()

    # 2. 无截图基准时回退到历史快照
    if not cash_base:
        goals = load_goals()
        initial = float(goals["target"]["initial_capital"]) if goals else 84000.0
        cash_base = _get_last_cash(initial)
        snap_date = None

    # 3. 计算截图日期之后的所有买卖现金变动
    if snap_date:
        sell_total, buy_total = _parse_trades_since(snap_date)
    else:
        sell_total, buy_total = 0.0, 0.0
    cash_flow = sell_total - buy_total

    # 4. 最终现金 = 截图基准 + 截图后的净流入
    estimated_cash = cash_base + cash_flow

    estimated_net = total_market_value + estimated_cash

    details.sort(key=lambda x: x["market_value"], reverse=True)
    return {
        "net_worth": round(estimated_net, 2),
        "market_value": round(total_market_value, 2),
        "estimated_cash": round(estimated_cash, 2),
        "cash_base": round(cash_base, 2),
        "cash_flow": round(cash_flow, 2),
        "sell_total": round(sell_total, 2),
        "buy_total": round(buy_total, 2),
        "positions": details,
        "position_count": len(positions),
    }


def _get_last_cash(fallback):
    """从历史快照取上次现金"""
    if not HISTORY_FILE.exists():
        return fallback
    try:
        with open(HISTORY_FILE) as f:
            history = json.load(f)
        if history and isinstance(history, list) and history[-1]:
            return history[-1].get("estimated_cash", fallback)
    except Exception:
        pass
    return fallback


def compute_progress(net_worth, goals):
    """计算对目标的进度"""
    target = goals["target"]
    initial = float(target["initial_capital"])
    target_val = float(target["value"])
    total_gain_needed = target_val - initial
    current_gain = net_worth - initial
    progress_pct = (current_gain / total_gain_needed) * 100 if total_gain_needed > 0 else 0

    try:
        start_dt = datetime.strptime(target["start_date"], "%Y-%m-%d")
        end_dt = datetime.strptime(target["deadline"], "%Y-%m-%d")
        now_dt = datetime.now()
        total_days = (end_dt - start_dt).days
        elapsed_days = (now_dt - start_dt).days
        time_progress = max(0, min(100, (elapsed_days / total_days) * 100))
        remaining_days = max(0, (end_dt - now_dt).days)
    except (ValueError, KeyError):
        time_progress = 50
        remaining_days = 250

    return {
        "net_worth": round(net_worth, 2),
        "target_value": target_val,
        "initial_capital": initial,
        "current_gain": round(current_gain, 2),
        "remaining_gain": round(max(0, target_val - net_worth), 2),
        "progress_pct": round(max(0, min(100, progress_pct)), 1),
        "time_progress_pct": round(time_progress, 1),
        "remaining_days": remaining_days,
        "monthly_return_needed_pct": round(
            (_monthly_return_needed(net_worth, target_val, remaining_days)), 1
        ) if remaining_days > 0 else 0,
    }


def _monthly_return_needed(current, target, remaining_days):
    if current <= 0 or remaining_days <= 0:
        return 0
    months = remaining_days / 30.0
    ratio = target / current
    if ratio <= 1:
        return 0
    return (ratio ** (1 / months) - 1) * 100


def compute_milestones(net_worth, goals):
    milestones = goals.get("milestones", [])
    status_list = []
    for ms in milestones:
        target_val = float(ms["target_value"])
        achieved = net_worth >= target_val
        progress = min(100, (net_worth / target_val) * 100) if target_val > 0 else 0
        gap = target_val - net_worth

        status_list.append({
            "id": ms["id"],
            "name": ms["name"],
            "target_value": target_val,
            "current_progress_pct": round(progress, 1),
            "gap": round(gap, 2),
            "achieved": achieved,
            "deadline": ms.get("deadline", ""),
        })

    return status_list


def compute_risk_stage(net_worth, goals):
    stages = goals.get("progressive_risk", {}).get("stages", [])
    default_rules = goals.get("risk_rules", {})

    for stage in stages:
        low, high = stage["net_worth_range"]
        if low <= net_worth < high:
            return {
                "stage": stage["description"],
                "max_single_position_pct": stage["max_single_position_pct"],
                "min_cash_reserve_pct": stage["min_cash_reserve_pct"],
                "max_positions": stage["max_positions"],
            }

    return {
        "stage": "默认",
        "max_single_position_pct": default_rules.get("max_single_position_pct", 0.25),
        "min_cash_reserve_pct": default_rules.get("min_cash_reserve_pct", 0.10),
        "max_positions": 6,
    }


def compute_alerts(net_worth, progress, risk_stage, goals):
    alerts = []
    thresholds = goals.get("alert_thresholds", {})
    total_drawdown_pct = ((net_worth - progress["initial_capital"]) /
                          progress["initial_capital"]) * 100

    critical = thresholds.get("drawdown_critical_pct", -12)
    warning = thresholds.get("drawdown_warning_pct", -8)
    if total_drawdown_pct <= critical:
        alerts.append({
            "level": "CRITICAL",
            "message": f"整体回撤 {total_drawdown_pct:.1f}% 触及临界线{critical}%，建议暂停新开仓",
        })
    elif total_drawdown_pct <= warning:
        alerts.append({
            "level": "WARNING",
            "message": f"整体回撤 {total_drawdown_pct:.1f}% 触及预警线{warning}%",
        })

    if progress["progress_pct"] < progress["time_progress_pct"] - thresholds.get("milestone_progress_deviation_pct", 20):
        alerts.append({
            "level": "WARNING",
            "message": f"净值进度 {progress['progress_pct']}% 落后时间进度 {progress['time_progress_pct']}%，差距>{thresholds.get('milestone_progress_deviation_pct', 20)}%",
        })

    return alerts


# ======================== 输出 ========================

def save_snapshot(snapshot):
    SNAPSHOT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)


def append_history(snapshot):
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    history = []
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE) as f:
                history = json.load(f)
        except Exception:
            history = []

    record_date = snapshot["date"]
    updated = False
    for item in history:
        if item.get("date") == record_date:
            item.update({
                "net_worth": snapshot["net_worth"],
                "market_value": snapshot["market_value"],
                "estimated_cash": snapshot["estimated_cash"],
                "progress_pct": snapshot["progress"]["progress_pct"],
                "timestamp": snapshot["timestamp"],
            })
            updated = True
            break

    if not updated:
        history.append({
            "date": record_date,
            "net_worth": snapshot["net_worth"],
            "market_value": snapshot["market_value"],
            "estimated_cash": snapshot["estimated_cash"],
            "progress_pct": snapshot["progress"]["progress_pct"],
            "timestamp": snapshot["timestamp"],
        })

    history = history[-365:]

    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def save_milestones(milestones):
    MILESTONE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(MILESTONE_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "updated": now_iso(),
            "milestones": milestones,
        }, f, ensure_ascii=False, indent=2)


def print_summary(snapshot):
    p = snapshot["progress"]
    rs = snapshot["risk_stage"]
    al = snapshot["alerts"]
    ms = snapshot["milestones"]

    print("=" * 50)
    print(f"📊 组合绩效快照 {snapshot['date']}")
    print(f"   净值: ¥{p['net_worth']:,.2f}  |  目标 {p['target_value']:,.0f}万  |  进度 {p['progress_pct']}%")
    print(f"   持仓 {snapshot['position_count']}只  |  市值 ¥{snapshot['market_value']:,.2f}")
    print(f"   现金 ¥{snapshot['estimated_cash']:,.2f} (基准¥{snapshot.get('cash_base',0):,.2f} + 今日净流¥{snapshot.get('cash_flow',0):+,.2f})")
    print(f"   剩余 {p['remaining_days']}天  |  月化需求 +{p['monthly_return_needed_pct']}%")
    print()

    print(f"🗂️  风控阶段: {rs['stage']}")
    print(f"   单票上限 {rs['max_single_position_pct']*100:.0f}% | 现金保留 {rs['min_cash_reserve_pct']*100:.0f}% | 最大持仓 {rs['max_positions']}只")
    print()

    if al:
        print("⚠️  告警:")
        for a in al:
            print(f"   [{a['level']}] {a['message']}")
        print()

    print("🏔️  里程碑:")
    for m in ms:
        icon = "✅" if m["achieved"] else "⬜"
        print(f"   {icon} {m['name']}: {m['current_progress_pct']:.1f}%  (差 ¥{m['gap']:,.0f})")

    print("=" * 50)


# ======================== main ========================

def run(goals_override=None):
    goals = goals_override or load_goals()
    if not goals:
        return {"error": "investment_goals.json 不存在或格式错误"}

    live_prices = _read_live_prices()
    positions = _parse_real_positions()

    if not positions:
        return {"error": "未能从 TOOLS.md 解析到有效持仓"}

    net_worth_data = compute_net_worth(positions, live_prices)
    net_worth = net_worth_data["net_worth"]

    progress = compute_progress(net_worth, goals)
    risk_stage = compute_risk_stage(net_worth, goals)
    milestones = compute_milestones(net_worth, goals)
    alerts = compute_alerts(net_worth, progress, risk_stage, goals)

    snapshot = {
        "date": today(),
        "timestamp": now_iso(),
        "net_worth": net_worth_data["net_worth"],
        "market_value": net_worth_data["market_value"],
        "estimated_cash": net_worth_data["estimated_cash"],
        "cash_base": net_worth_data.get("cash_base", 0),
        "cash_flow": net_worth_data.get("cash_flow", 0),
        "sell_total": net_worth_data.get("sell_total", 0),
        "buy_total": net_worth_data.get("buy_total", 0),
        "position_count": net_worth_data["position_count"],
        "positions": net_worth_data["positions"],
        "progress": progress,
        "risk_stage": risk_stage,
        "milestones": milestones,
        "alerts": alerts,
    }

    return snapshot


def main():
    snapshot = run()
    if "error" in snapshot:
        print(f"❌ {snapshot['error']}")
        sys.exit(1)

    save_snapshot(snapshot)
    append_history(snapshot)
    save_milestones(snapshot["milestones"])
    print_summary(snapshot)


if __name__ == "__main__":
    main()
