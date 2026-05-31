#!/usr/bin/env python3
"""
里程碑告警引擎 (Milestone Alert Engine)
========================================
职责：对比当前净值进度与时间进度，在偏离超过阈值时推送告警。

依赖：
  1. data/investment_goals.json — 告警阈值配置
  2. portfolio_engine.py — 净值计算（import 作为模块）

使用方式：
  python3 scripts/milestone_alert.py          # 标准检查（无告警则静默）
  python3 scripts/milestone_alert.py --force  # 强制输出（即使无告警）
  
输出（供cron推送辉哥）:
  标准模式：无告警则输出空，有告警输出结构化消息
  --force模式：输出完整里程碑面板
"""

import json
import os
import sys
from datetime import datetime, date
from pathlib import Path

WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace"))
GOALS_FILE = WORKSPACE / "data" / "investment_goals.json"
ALERT_LOG = WORKSPACE / "data" / "milestone_alerts.json"

# 添加 scripts 到路径以 import portfolio_engine
sys.path.insert(0, str(WORKSPACE / "scripts"))
from portfolio_engine import load_goals, compute_progress, compute_milestones, compute_risk_stage, compute_alerts, _read_live_prices, _parse_real_positions, compute_net_worth


def log_alert(alert_msg, today_str):
    """记录告警到文件，附带时间戳"""
    ALERT_LOG.parent.mkdir(parents=True, exist_ok=True)
    log = []
    if ALERT_LOG.exists():
        try:
            with open(ALERT_LOG) as f:
                log = json.load(f)
        except Exception:
            log = []

    log.append({
        "date": today_str,
        "timestamp": datetime.now().isoformat(),
        "message": alert_msg,
    })

    # 保留最近 90 条
    log = log[-90:]
    with open(ALERT_LOG, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)


def generate_message(net_worth, progress, risk_stage, milestones, alerts):
    """生成辉哥可读的推送消息"""
    p = progress
    rs = risk_stage
    ms = milestones
    al = alerts

    lines = [
        f"📊 净值 ¥{p['net_worth']:,.0f} | 目标进度 {p['progress_pct']}% | 时间进度 {p['time_progress_pct']}%",
        f"",
    ]

    # 进度偏差
    gap = p['progress_pct'] - p['time_progress_pct']
    if gap >= 0:
        lines.append(f"📈 净值领先时间线 +{gap:.1f}pp")
    else:
        lines.append(f"📉 净值落后时间线 {gap:.1f}pp | 差 ¥{p['remaining_gain']:,.0f} | 需月化 +{p['monthly_return_needed_pct']}%")
    lines.append("")

    # 告警
    if al:
        lines.append("⚠️ 告警:")
        for a in al:
            icon = "🔴" if a["level"] == "CRITICAL" else "🟡"
            lines.append(f"  {icon} {a['message']}")
        lines.append("")

    # 里程碑
    lines.append(f"🏔️ 里程碑（风控: {rs['stage']}）")
    for m in ms:
        icon = "✅" if m["achieved"] else "⬜"
        target_desc = f"¥{m['target_value']:,.0f}"
        gap_desc = f"差 ¥{m['gap']:,.0f}" if m['gap'] > 0 else f"超 ¥{abs(m['gap']):,.0f}"
        deadline = f"｜{m['deadline']}" if m.get('deadline') else ""
        lines.append(f"  {icon} {m['name']}: {m['current_progress_pct']:.0f}% ({target_desc} {gap_desc}){deadline}")

    lines.append("")
    lines.append("— 量化引擎 · 里程碑面板")

    return "\n".join(lines)


def run(force=False):
    goals = load_goals()
    if not goals:
        return {"error": "无法加载投资目标配置"}

    live_prices = _read_live_prices()
    positions = _parse_real_positions()

    if not positions:
        return {"error": "无有效持仓"}

    net_worth_data = compute_net_worth(positions, live_prices)
    net_worth = net_worth_data["net_worth"]

    progress = compute_progress(net_worth, goals)
    risk_stage = compute_risk_stage(net_worth, goals)
    milestones = compute_milestones(net_worth, goals)
    alerts = compute_alerts(net_worth, progress, risk_stage, goals)

    # 标准模式：无告警则静默
    if not alerts and not force:
        return None

    message = generate_message(net_worth, progress, risk_stage, milestones, alerts)
    today_str = date.today().isoformat()

    # 有告警时记录
    if alerts:
        for a in alerts:
            log_alert(a["message"], today_str)

    return {"message": message, "alerts": alerts, "net_worth": net_worth, "progress": progress}


def main():
    force = "--force" in sys.argv
    result = run(force=force)
    if result and "error" in result:
        print(f"❌ {result['error']}")
        sys.exit(1)
    if result and result.get("message"):
        print(result["message"])
    # 无告警时什么也不输出（静默）


if __name__ == "__main__":
    main()
