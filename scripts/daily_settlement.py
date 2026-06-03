#!/usr/bin/env python3
"""
盘后交割单生成器 (Post-market Settlement Generator)
==================================================
整合 portfolio_engine 每日快照，生成结构化交割单 JSON，供盘后cron直接推送。

数据链路：
  pipeline.sh 收盘轮 → engine_signals.json
                      → portfolio_engine.run() → portfolio_snapshot.json
                      → settlement.py read → settlement.json → cron推送

输出：
  data/settlement.json        — 当日交割单结构化数据
  stdout                      — Markdown 格式摘要（可直接推送）
"""

import json
import os
import sys
from datetime import datetime, date
from pathlib import Path

WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace"))
SNAPSHOT_FILE = WORKSPACE / "data" / "portfolio_snapshot.json"
GOALS_FILE = WORKSPACE / "data" / "investment_goals.json"
HISTORY_FILE = WORKSPACE / "data" / "portfolio_history.json"
SETTLEMENT_FILE = WORKSPACE / "data" / "settlement.json"

sys.path.insert(0, str(WORKSPACE / "scripts"))
from portfolio_engine import load_goals, _parse_real_positions, _read_live_prices, compute_net_worth, compute_progress, compute_risk_stage, compute_milestones, compute_alerts, save_snapshot


def load_or_compute_snapshot():
    """优先从已有快照读，否则重新计算，并写回快照文件"""
    if SNAPSHOT_FILE.exists():
        try:
            with open(SNAPSHOT_FILE) as f:
                snap = json.load(f)
            if snap.get("date") == date.today().isoformat():
                return snap
        except Exception:
            pass

    # 重新计算
    goals = load_goals()
    lives = _read_live_prices()
    positions = _parse_real_positions()
    nw = compute_net_worth(positions, lives)
    progress = compute_progress(nw["net_worth"], goals or {})
    risk = compute_risk_stage(nw["net_worth"], goals or {})
    milestones = compute_milestones(nw["net_worth"], goals or {})
    alerts = compute_alerts(nw["net_worth"], progress, risk, goals or {})

    snap = {
        "date": date.today().isoformat(),
        "timestamp": datetime.now().isoformat(),
        "net_worth": nw["net_worth"],
        "market_value": nw["market_value"],
        "estimated_cash": nw["estimated_cash"],
        "position_count": nw["position_count"],
        "positions": nw["positions"],
        "progress": progress,
        "risk_stage": risk,
        "milestones": milestones,
        "alerts": alerts,
    }
    
    # 写回快照文件，确保其他脚本能同步
    save_snapshot(snap)
    return snap


def calc_daily_change():
    """计算今日净值变化（对比昨日历史）"""
    if not HISTORY_FILE.exists():
        return 0, 0
    try:
        with open(HISTORY_FILE) as f:
            history = json.load(f)
    except Exception:
        return 0, 0

    history_sorted = sorted(history, key=lambda x: x.get("date", ""))
    if len(history_sorted) < 2:
        return 0, 0

    today_val = history_sorted[-1].get("net_worth", 0)
    prev_val = history_sorted[-2].get("net_worth", 0)
    if prev_val == 0:
        return 0, 0

    change = today_val - prev_val
    change_pct = (change / prev_val) * 100
    return round(change, 2), round(change_pct, 2)


def calc_position_attribution(snap):
    """持仓盈亏归因：每只票贡献了多少盈亏"""
    positions = snap.get("positions", [])
    attribution = []
    total_pnl = 0
    for p in positions:
        pnl = p.get("pnl", 0) if p.get("pnl") is not None else 0
        total_pnl += pnl
        attribution.append({
            "code": p["code"],
            "name": p["name"],
            "shares": p["shares"],
            "pnl": pnl,
            "pnl_pct": p.get("pnl_pct", 0),
            "weight_pct": round(p["market_value"] / snap["net_worth"] * 100, 1) if snap["net_worth"] > 0 else 0,
        })
    attribution.sort(key=lambda x: x["pnl"], reverse=True)
    return attribution, total_pnl


def calc_weekly_pnl():
    """最近5个交易日的周盈亏"""
    if not HISTORY_FILE.exists():
        return 0, 0
    try:
        with open(HISTORY_FILE) as f:
            history = json.load(f)
    except Exception:
        return 0, 0

    history_sorted = sorted(history, key=lambda x: x.get("date", ""))
    week = history_sorted[-5:]
    if len(week) < 2:
        return 0, 0

    start = week[0]["net_worth"]
    end = week[-1]["net_worth"]
    change = end - start
    change_pct = (change / start * 100) if start > 0 else 0
    return round(change, 2), round(change_pct, 2)


def calc_monthly_pnl():
    """本月累计盈亏"""
    if not HISTORY_FILE.exists():
        return 0, 0
    try:
        with open(HISTORY_FILE) as f:
            history = json.load(f)
    except Exception:
        return 0, 0

    month_prefix = date.today().strftime("%Y-%m")
    month_data = sorted(
        [h for h in history if h.get("date", "").startswith(month_prefix)],
        key=lambda x: x.get("date", "")
    )
    if len(month_data) < 2:
        return 0, 0

    start = month_data[0]["net_worth"]
    end = month_data[-1]["net_worth"]
    change = end - start
    change_pct = (change / start * 100) if start > 0 else 0
    return round(change, 2), round(change_pct, 2)


def load_cumulative_pnl():
    """从 goals 配置算累计已结盈亏——目前从 TOOLS.md 解析清仓记录
    替代方案：如果有 data/cumulative_pnl.json 优先读"""
    cumulative_file = WORKSPACE / "data" / "cumulative_pnl.json"
    if cumulative_file.exists():
        try:
            with open(cumulative_file) as f:
                return json.load(f).get("total", 0)
        except Exception:
            pass

    # fallback: 从 TOOLS.md 解析已结盈亏
    tools = WORKSPACE / "TOOLS.md"
    total = 0
    if tools.exists():
        import re
        text = tools.read_text(encoding="utf-8")
        for line in text.splitlines():
            m = re.search(r'[盈亏][+]?([\d,]+).*?[¥￥]([\d,]+)', line)
            if m:
                try:
                    amt = float(m.group(2).replace(',', ''))
                    if '亏' in line and '清仓' in line:
                        total -= amt
                    elif '盈' in line and '清仓' in line:
                        total += amt
                except ValueError:
                    pass
    return round(total, 2)


def generate_settlement(snap):
    """生成完整交割单"""
    daily_change, daily_change_pct = calc_daily_change()
    weekly_pnl, weekly_pnl_pct = calc_weekly_pnl()
    monthly_pnl, monthly_pnl_pct = calc_monthly_pnl()
    attribution, total_pnl = calc_position_attribution(snap)
    cumulative_pnl = load_cumulative_pnl()

    settlement = {
        "generated_at": datetime.now().isoformat(),
        "date": snap["date"],
        # 净值
        "net_worth": snap["net_worth"],
        "market_value": snap["market_value"],
        "estimated_cash": snap["estimated_cash"],
        "daily_change": daily_change,
        "daily_change_pct": daily_change_pct,
        # 周期盈亏
        "weekly_pnl": weekly_pnl,
        "weekly_pnl_pct": weekly_pnl_pct,
        "monthly_pnl": monthly_pnl,
        "monthly_pnl_pct": monthly_pnl_pct,
        "cumulative_realized_pnl": cumulative_pnl,
        # 持仓
        "position_count": snap["position_count"],
        "positions": snap["positions"],
        "attribution": attribution,
        "total_holding_pnl": total_pnl,
        # 目标
        "progress": snap["progress"],
        "risk_stage": snap["risk_stage"],
        "milestones": snap["milestones"],
        # 告警
        "alerts": snap["alerts"],
    }

    # 保存
    SETTLEMENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SETTLEMENT_FILE, "w", encoding="utf-8") as f:
        json.dump(settlement, f, ensure_ascii=False, indent=2)

    return settlement


def print_markdown(s):
    """输出辉哥可读的 Markdown 交割单"""
    p = s["progress"]
    rs = s["risk_stage"]
    ms = s["milestones"]
    al = s["alerts"]

    lines = [
        f"# 📋 盘后交割单 {s['date']}",
        "",
        f"## 💰 账户总览",
        f"",
        f"净值 **¥{s['net_worth']:,.0f}** | 日变动 {s['daily_change']:+,.0f} ({s['daily_change_pct']:+.1f}%)",
        f"",
        f"| 周期 | 盈亏 | 涨跌 |",
        f"|:-----|:-----|:-----|",
        f"| 今日 | ¥{s['daily_change']:+,.0f} | {s['daily_change_pct']:+.1f}% |",
        f"| 本周 | ¥{s['weekly_pnl']:+,.0f} | {s['weekly_pnl_pct']:+.1f}% |",
        f"| 本月 | ¥{s['monthly_pnl']:+,.0f} | {s['monthly_pnl_pct']:+.1f}% |",
        f"| 已结 | ¥{s['cumulative_realized_pnl']:+,.0f} | — |",
        f"",
        f"持仓 {s['position_count']}只 | 市值 ¥{s['market_value']:,.0f} | 现金 ~¥{s['estimated_cash']:,.0f}",
        f"",
        f"## 🎯 年度目标",
        f"",
        f"进度 **{p['progress_pct']}%** ({p['time_progress_pct']}% 时间) | 差距 ¥{p['remaining_gain']:,.0f} | 月化 +{p['monthly_return_needed_pct']}%",
        f"",
        f"风控: {rs['stage']} | 单票≤{rs['max_single_position_pct']*100:.0f}% | 现金≥{rs['min_cash_reserve_pct']*100:.0f}%",
        f"",
    ]

    # 持仓明细
    if s["positions"]:
        lines.append(f"## 📊 持仓明细")
        lines.append("")
        for pos in s["positions"]:
            pnl_str = f"{pos.get('pnl', 0):+,.0f} ({pos.get('pnl_pct', 0):+.1f}%)"
            lines.append(f"- **{pos['name']}** {pos['code']} {pos['shares']}股 @{pos.get('current_price', 0):.2f} 成本{pos['cost']:.2f} 浮{pos.get('pnl', 0):+,.0f}")
        lines.append("")

    # 盈亏归因
    if s["attribution"]:
        lines.append(f"## 📈 盈亏归因")
        lines.append("")
        for a in s["attribution"]:
            icon = "🟢" if a["pnl"] > 0 else "🔴"
            lines.append(f"- {icon} {a['name']}: {a['pnl']:+,.0f} ({a['pnl_pct']:+.1f}%) 权重{a['weight_pct']}%")
        lines.append("")

    # 里程碑
    lines.append(f"## 🏔️ 里程碑")
    lines.append("")
    for m in ms:
        icon = "✅" if m["achieved"] else "⬜"
        lines.append(f"- {icon} {m['name']}: {m['current_progress_pct']:.0f}% → ¥{m['target_value']:,.0f} 差¥{m['gap']:,.0f}")
    lines.append("")

    # 告警
    if al:
        lines.append(f"## ⚠️ 告警")
        lines.append("")
        for a in al:
            lines.append(f"- [{a['level']}] {a['message']}")
        lines.append("")

    lines.append(f"---")
    lines.append(f"*量化引擎自动生成 · {datetime.now().strftime('%Y-%m-%d %H:%M')}*")

    return "\n".join(lines)


def main():
    snap = load_or_compute_snapshot()
    settlement = generate_settlement(snap)
    md = print_markdown(settlement)
    print(md)


if __name__ == "__main__":
    main()
