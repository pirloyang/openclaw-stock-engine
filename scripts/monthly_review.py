#!/usr/bin/env python3
"""
月度绩效审计引擎 (Monthly Review Engine)
========================================
每月最后一个交易日自动生成结构化月报。

数据源：
  data/portfolio_history.json  — 净值时间序列
  data/investment_goals.json    — 目标基线
  data/monthly_reviews/         — 历史月报归档

输出：
  reports/monthly/YYYY-MM.md  — Markdown 月报
  data/monthly_snapshot.json  — 月报结构化数据(供后续对比)
"""

import json
import os
import sys
from datetime import datetime, date, timedelta
from pathlib import Path
from collections import defaultdict

WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace"))
GOALS_FILE = WORKSPACE / "data" / "investment_goals.json"
HISTORY_FILE = WORKSPACE / "data" / "portfolio_history.json"
TOOLS_FILE = WORKSPACE / "TOOLS.md"
REPORTS_DIR = WORKSPACE / "reports" / "monthly"
SNAPSHOT_FILE = WORKSPACE / "data" / "monthly_snapshot.json"

sys.path.insert(0, str(WORKSPACE / "scripts"))
from portfolio_engine import load_goals, _parse_real_positions, _read_live_prices, compute_net_worth, compute_progress, compute_risk_stage


def current_month_str():
    return date.today().strftime("%Y-%m")


def month_start_date():
    """本月第一天"""
    dt = date.today().replace(day=1)
    return dt.strftime("%Y-%m-%d")


def load_month_data():
    """从 history 中提取本月的净值数据"""
    if not HISTORY_FILE.exists():
        return []
    try:
        with open(HISTORY_FILE) as f:
            history = json.load(f)
    except Exception:
        return []

    month_prefix = current_month_str()
    return [h for h in history if h.get("date", "").startswith(month_prefix)]


def calc_month_stats(month_data):
    """计算月统计"""
    if not month_data:
        return {}
    records = sorted(month_data, key=lambda x: x.get("date", ""))
    start_val = records[0].get("net_worth") if records else 0
    end_val = records[-1].get("net_worth") if records else 0
    change = end_val - start_val
    change_pct = (change / start_val * 100) if start_val > 0 else 0

    # 月内最高/最低
    peak = max((r.get("net_worth", 0) for r in records), default=0)
    trough = min((r.get("net_worth", 0) for r in records), default=0)
    max_drawdown_pct = ((trough - peak) / peak * 100) if peak > 0 else 0

    return {
        "month": current_month_str(),
        "start_value": round(start_val, 2),
        "end_value": round(end_val, 2),
        "change": round(change, 2),
        "change_pct": round(change_pct, 2),
        "peak_value": round(peak, 2),
        "trough_value": round(trough, 2),
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "trading_days": len(records),
    }


def calc_trade_summary():
    """从 TOOLS.md 解析本月清仓记录"""
    summary = {"closed": [], "total_pnl": 0, "total_pnl_pct": 0}
    if not TOOLS_FILE.exists():
        return summary
    try:
        text = TOOLS_FILE.read_text(encoding="utf-8")
    except Exception:
        return summary

    # 查找当月清仓记录，找出清仓日期块的标题
    # 格式: ### 今日清仓记录（2026-05-27）
    month_prefix = current_month_str()[:7]  # "2026-06"
    in_section = False
    section_date = ""
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r'###\s+今日清仓记录（(\d{4}-\d{2}-\d{2})）', line)
        if m:
            section_date = m.group(1)
            in_section = section_date.startswith(month_prefix)
            continue

        if line.startswith('###') and '清仓' not in line:
            in_section = False
            continue

        if not in_section:
            continue

        # 解析: - 名称 代码：清仓@XX，盈/亏+XX%（¥XX）
        m = re.match(r'-\s*(.+?)\s+(\d{6})：.*?清仓@([\d.]+).*?([盈亏])\+?([\d.]+)%.*?[¥￥]([\d,]+)', line)
        if m:
            name = m.group(1).strip()
            code = m.group(2)
            price = float(m.group(3))
            direction = m.group(4)
            pnl_pct = float(m.group(5))
            pnl_amount = float(m.group(6).replace(',', ''))
            if direction == '亏':
                pnl_amount = -pnl_amount
                pnl_pct = -pnl_pct
            summary["closed"].append({
                "code": code, "name": name, "price": price,
                "pnl_amount": pnl_amount, "pnl_pct": pnl_pct,
                "date": section_date,
            })
            summary["total_pnl"] += pnl_amount

    if summary["closed"]:
        total_deals = len(summary["closed"])
        summary["win_count"] = sum(1 for d in summary["closed"] if d["pnl_amount"] > 0)
        summary["lose_count"] = sum(1 for d in summary["closed"] if d["pnl_amount"] < 0)
        summary["win_rate"] = round(summary["win_count"] / total_deals * 100, 1) if total_deals > 0 else 0

    return summary


def generate_markdown(month_stats, trade_summary, progress, risk_stage, positions):
    """生成 Markdown 月报"""
    ms = month_stats
    ts = trade_summary
    p = progress
    rs = risk_stage

    lines = [
        f"# 📊 月度绩效审计 — {ms['month']}",
        "",
        "## 1. 账户概览",
        "",
        f"| 指标 | 数值 |",
        f"|:-----|:-----|",
        f"| 月初净值 | ¥{ms['start_value']:,.0f} |",
        f"| 月末净值 | ¥{ms['end_value']:,.0f} |",
        f"| 月盈亏 | {ms['change']:+,.0f} ({ms['change_pct']:+.1f}%) |",
        f"| 月内峰值 | ¥{ms['peak_value']:,.0f} |",
        f"| 月内谷值 | ¥{ms['trough_value']:,.0f} |",
        f"| 最大回撤 | {ms['max_drawdown_pct']:.1f}% |",
        f"| 交易日 | {ms['trading_days']} 天 |",
        "",
        "## 2. 年度目标进度",
        "",
        f"| 指标 | 数值 |",
        f"|:-----|:-----|",
        f"| 当前净值 | ¥{p['net_worth']:,.0f} |",
        f"| 年度目标 | ¥{p['target_value']:,.0f} |",
        f"| 目标进度 | {p['progress_pct']}% |",
        f"| 时间进度 | {p['time_progress_pct']}% |",
        f"| 剩余天数 | {p['remaining_days']} 天 |",
        f"| 月化需求 | +{p['monthly_return_needed_pct']}% |",
        f"| 风控阶段 | {rs['stage']} |",
        "",
    ]

    # 清仓记录
    if ts["closed"]:
        lines.append("## 3. 本月清仓记录")
        lines.append("")
        lines.append(f"| 标的 | 代码 | 清仓价 | 盈亏 | 日期 |")
        lines.append(f"|:-----|:----|:------|:----|:----|")
        for t in ts["closed"]:
            icon = "📈" if t["pnl_amount"] > 0 else "📉"
            lines.append(f"| {t['name']} | {t['code']} | {t['price']} | {icon} {t['pnl_pct']:+.1f}% (¥{t['pnl_amount']:+,.0f}) | {t['date']} |")
        lines.append("")
        total_icon = "✅ 净盈利" if ts["total_pnl"] > 0 else "🔴 净亏损"
        if ts.get("win_rate") is not None:
            lines.append(f"**{total_icon} ¥{ts['total_pnl']:+,.0f}** | {ts['win_count']}胜{ts['lose_count']}负 | 胜率 {ts['win_rate']}%")
        else:
            lines.append(f"**{total_icon} ¥{ts['total_pnl']:+,.0f}**")
        lines.append("")

    # 当前持仓
    lines.append("## 4. 月末持仓")
    lines.append("")
    if positions:
        lines.append(f"| 标的 | 代码 | 股数 | 成本 | 现价 | 市值 | 浮盈 |")
        lines.append(f"|:-----|:----|:----|:----|:----|:----|:----|")
        for pos in positions:
            pnl_str = f"{pos.get('pnl', 0):+,.0f} ({pos.get('pnl_pct', 0):+.1f}%)"
            lines.append(f"| {pos['name']} | {pos['code']} | {pos['shares']} | {pos['cost']:.2f} | {pos.get('current_price', 0):.2f} | ¥{pos.get('market_value', 0):,.0f} | {pnl_str} |")
        lines.append("")

    # 风险与建议（占位，实际由 AI 填充）
    lines.append("## 5. AI 策略评估与建议")
    lines.append("")
    lines.append("> *本节由盘中监控AI在月报推送后补充填写*")
    lines.append("")

    # 生成信息
    lines.append(f"---")
    lines.append(f"*由组合管理引擎自动生成 · {datetime.now().strftime('%Y-%m-%d %H:%M')}*")

    return "\n".join(lines)


def main():
    month_data = load_month_data()
    month_stats = calc_month_stats(month_data)

    goals = load_goals()
    positions = _parse_real_positions()
    live_prices = _read_live_prices()
    net_worth_data = compute_net_worth(positions, live_prices)
    progress = compute_progress(net_worth_data["net_worth"], goals or {})
    risk_stage = compute_risk_stage(net_worth_data["net_worth"], goals or {})

    trade_summary = calc_trade_summary()
    md = generate_markdown(month_stats, trade_summary, progress, risk_stage, positions)

    # 保存月报
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"{current_month_str()}.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(md)

    # 保存结构化快照
    SNAPSHOT_FILE.parent.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "month": current_month_str(),
        "month_stats": month_stats,
        "progress": progress,
        "trade_summary": {
            "closed_count": len(trade_summary.get("closed", [])),
            "total_pnl": trade_summary.get("total_pnl", 0),
            "win_rate": trade_summary.get("win_rate"),
        },
        "positions_count": len(positions),
        "generated_at": datetime.now().isoformat(),
    }
    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    print(f"✅ 月报已生成: {report_path}")
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    import re
    main()
