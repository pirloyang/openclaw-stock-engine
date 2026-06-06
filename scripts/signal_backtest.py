#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
信号回测验证脚本 v1.0
扫描历史报告，统计三重共振/双重确认/单一信号/空头信号的真实胜率。
- 输入：reports/ 下所有日报告 + data/portfolio_history.json
- 输出：data/signal_backtest_latest.json + data/signal_backtest_history.json
- 触发：每月末 19:00 cron 自动运行
"""
import os
import re
import json
import sys
import subprocess
from pathlib import Path
from datetime import datetime, timedelta

WORKSPACE = Path("/root/.openclaw/workspace")
REPORTS_DIR = WORKSPACE / "reports"
HISTORY_FILE = WORKSPACE / "data" / "signal_backtest_history.json"
LATEST_FILE = WORKSPACE / "data" / "signal_backtest_latest.json"

# 期望胜率基线
EXPECT = {
    "triple_resonance": {"T+5": 0.65, "T+10": 0.70, "T+20": 0.60},
    "dual_confirm":     {"T+5": 0.55, "T+10": 0.55, "T+20": 0.50},
    "single":           {"T+5": 0.50, "T+10": 0.50, "T+20": 0.45},
    "bearish":          {"T+5": 0.60, "T+10": 0.65, "T+20": 0.55},
}
ALERT_DELTA = 0.10  # 低于期望 10pp 触发告警
HIT_THRESHOLD = 0.03  # 涨/跌幅 3% 视为命中

# 信号识别正则
SIGNAL_PATTERNS = {
    "triple_resonance": [r"三重共振[^a-zA-Z]*?([\u4e00-\u9fa5A-Z]+)\s*[（(]?(\d{6})", r"出手[^a-zA-Z]*?([\u4e00-\u9fa5A-Z]+)\s*[（(]?(\d{6})"],
    "dual_confirm":     [r"双重确认[^a-zA-Z]*?([\u4e00-\u9fa5A-Z]+)\s*[（(]?(\d{6})"],
    "single":           [r"单一信号[^a-zA-Z]*?([\u4e00-\u9fa5A-Z]+)\s*[（(]?(\d{6})"],
    "bearish":          [r"看空[^a-zA-Z]*?([\u4e00-\u9fa5A-Z]+)\s*[（(]?(\d{6})", r"减仓[^a-zA-Z]*?([\u4e00-\u9fa5A-Z]+)\s*[（(]?(\d{6})"],
}


def scan_reports(days_back=60):
    """扫描过去 N 天报告，提取信号推荐"""
    cutoff = datetime.now() - timedelta(days=days_back)
    recommendations = []
    for f in sorted(REPORTS_DIR.glob("2026-*-*.md")):
        m = re.match(r"(\d{4}-\d{2}-\d{2})-(\d{4})-(.+)\.md", f.name)
        if not m:
            continue
        date_str, hhmm, kind = m.groups()
        report_date = datetime.strptime(date_str, "%Y-%m-%d")
        if report_date < cutoff:
            continue
        text = f.read_text(encoding="utf-8")
        for sig_class, patterns in SIGNAL_PATTERNS.items():
            for pat in patterns:
                for match in re.finditer(pat, text):
                    name, code = match.groups()
                    recommendations.append({
                        "date": date_str,
                        "code": code,
                        "name": name,
                        "signal_class": sig_class,
                        "source": f.name,
                    })
    # 去重（同日同标的同信号取一次）
    seen = set()
    dedup = []
    for r in recommendations:
        key = (r["date"], r["code"], r["signal_class"])
        if key not in seen:
            seen.add(key)
            dedup.append(r)
    return dedup


def fetch_price(code, date_str):
    """从腾讯接口取指定日期收盘价（简化，使用现价兜底）"""
    # 真实场景应使用 akshare 历史数据；此处简化
    try:
        prefix = "sh" if code.startswith(("6", "9")) else "sz"
        url = f"http://qt.gtimg.cn/q={prefix}{code}"
        r = subprocess.run(["curl", "-s", url], capture_output=True, text=True, timeout=5)
        data = r.stdout
        m = re.search(r'~([\d.]+)~', data)
        return float(m.group(1)) if m else None
    except Exception:
        return None


def compute_outcome(rec, current_prices):
    """计算单条推荐的 T+5/T+10/T+20 命中情况"""
    code = rec["code"]
    rec_date = datetime.strptime(rec["date"], "%Y-%m-%d")
    today = datetime.now()
    days_passed = (today - rec_date).days
    # 当前价（简化，缺历史数据用现价代替）
    if code not in current_prices:
        current_prices[code] = fetch_price(code, None)
    cur_price = current_prices[code]
    if not cur_price:
        return None
    # 简化：使用现价 vs 推荐日的逻辑——recommend_price 无法获取，先用 cur_price 占位
    # 真实场景需要补充推荐日的开盘价数据源
    rec["current_price"] = cur_price
    rec["days_passed"] = days_passed
    return rec


def compute_winrates(records):
    """统计各信号类别的胜率（简化版：基于现价 vs 推荐日相对涨跌评估）"""
    # 注：此版本因缺少历史价数据，仅做样本量统计 + 现价快照
    # 完整版需要接入 akshare 历史 K 线
    stats = {}
    for sig_class in EXPECT:
        sig_records = [r for r in records if r["signal_class"] == sig_class]
        stats[sig_class] = {
            "sample_size": len(sig_records),
            "T+5_winrate": None,  # 占位，待历史价接入
            "T+10_winrate": None,
            "T+20_winrate": None,
            "expect": EXPECT[sig_class],
            "alert": None,
            "note": "v1.0 简化版：仅统计样本量，胜率计算需接入 akshare 历史 K 线",
        }
    return stats


def main():
    records = scan_reports(days_back=30)
    current_prices = {}
    outcomes = [compute_outcome(r, current_prices) for r in records]
    outcomes = [o for o in outcomes if o]
    stats = compute_winrates(outcomes)

    result = {
        "audit_time": datetime.now().isoformat(),
        "scan_window_days": 30,
        "total_recommendations": len(records),
        "by_class": stats,
        "samples": outcomes[:50],  # 仅保留前 50 条样本供检查
        "version": "v1.0",
        "limitation": "本版本因缺少历史 K 线数据，胜率字段为 null；样本量字段可用。完整胜率请等待 v2.0 (接入 akshare)。",
    }

    LATEST_FILE.parent.mkdir(exist_ok=True)
    LATEST_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    # 追加到历史
    history = []
    if HISTORY_FILE.exists():
        try:
            history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            history = []
    history.append({
        "audit_time": result["audit_time"],
        "total_recommendations": result["total_recommendations"],
        "by_class_sample_size": {k: v["sample_size"] for k, v in stats.items()},
    })
    HISTORY_FILE.write_text(json.dumps(history[-12:], ensure_ascii=False, indent=2), encoding="utf-8")

    # 控制台摘要
    print(f"=== 信号回测验证 · {datetime.now().strftime('%Y-%m-%d')} ===")
    print(f"扫描窗口: 过去 30 天")
    print(f"总推荐数: {len(records)}")
    for cls, s in stats.items():
        print(f"  {cls}: 样本 {s['sample_size']}")
    print(f"\n⚠️ v1.0 简化版：胜率计算待补充历史 K 线数据源")
    print(f"\n结果写入: {LATEST_FILE}")


if __name__ == "__main__":
    main()
