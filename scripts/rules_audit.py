#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
规则执行体检脚本 v1.0
扫描 reports/ 下今日/本周报告，验证：
1. R16 风控自检区块是否存在
2. R11 信号一票否决（信号看空时是否给相反建议）
3. R13 白名单（已清仓标的是否出现在持仓段）
4. SOP-M3 一致性（TOOLS.md 持仓 ↔ focus_watchlist hold）
5. 数据源链可追溯性

输出 JSON 报告 + 控制台摘要。
"""
import os
import re
import json
import sys
from pathlib import Path
from datetime import datetime, timedelta

WORKSPACE = Path("/root/.openclaw/workspace")
REPORTS_DIR = WORKSPACE / "reports"
TOOLS_MD = WORKSPACE / "TOOLS.md"
FOCUS_JSON = WORKSPACE / "stock-signals" / "focus_watchlist.json"

TODAY = os.environ.get("AUDIT_DATE", datetime.now().strftime("%Y-%m-%d"))
WEEK_AGO = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")


def parse_holdings_from_tools():
    """从 TOOLS.md 持仓节解析当前持仓代码列表"""
    if not TOOLS_MD.exists():
        return []
    text = TOOLS_MD.read_text(encoding="utf-8")
    holdings = []
    in_block = False
    for line in text.split("\n"):
        if re.match(r"^###\s*持仓", line):
            in_block = True
            continue
        if in_block and line.startswith("##"):
            break
        if in_block and "【" in line and "清仓" not in line:
            # 形如 - 长江通信 600345（200股...
            m = re.search(r"([\u4e00-\u9fa5A-Z0-9]+)\s+(\d{6})", line)
            if m:
                holdings.append({"name": m.group(1), "code": m.group(2)})
    return holdings


def parse_cleared_from_tools():
    """从 TOOLS.md 清仓记录解析已清仓代码列表（排除：当前仍在持仓节的标的）"""
    if not TOOLS_MD.exists():
        return []
    text = TOOLS_MD.read_text(encoding="utf-8")
    cleared = set()
    for m in re.finditer(r"([\u4e00-\u9fa5A-Z0-9]+)\s+(\d{6})[^\n]*清仓", text):
        cleared.add(m.group(2))
    # 排除当前仍持仓的（如长盈精密 300115 误报清仓后又确认持仓）
    current = {h["code"] for h in parse_holdings_from_tools()}
    cleared -= current
    return sorted(cleared)


def load_focus_watchlist():
    if not FOCUS_JSON.exists():
        return []
    try:
        return json.loads(FOCUS_JSON.read_text(encoding="utf-8"))
    except Exception:
        return []


def check_report(report_path: Path, holdings, cleared):
    """对单份报告执行 5 项检查"""
    if not report_path.exists():
        return {"path": str(report_path), "exists": False}
    text = report_path.read_text(encoding="utf-8")
    checks = {
        "path": str(report_path.relative_to(WORKSPACE)),
        "exists": True,
        "size": len(text),
    }
    # 1. R16 风控自检
    has_r16 = "【风控自检" in text or "风控自检" in text
    checks["R16_self_check"] = "✅" if has_r16 else "❌"
    # 2. R13 白名单：已清仓标的不应出现在"持仓"段（排除"清仓记录/复盘/历史交易"段）
    violations_r13 = []
    # 只检查"当前持仓"或"持仓明细"标题段（非清仓段、非复盘段、非历史段）
    for sec in re.finditer(r"(##\s*[^\n]*持仓[^\n]*)\n(.*?)(?=\n##\s|\Z)", text, re.DOTALL):
        title = sec.group(1)
        seg = sec.group(2)
        # 跳过清仓/复盘/历史段
        if any(k in title for k in ["清仓", "复盘", "历史", "已清仓", "了结"]):
            continue
        for code in cleared:
            # 只标记真正出现在"现有持仓"列表条目中的代码（而非提及）
            if re.search(rf"[-|*]\s*[^\n]*{code}[^\n]*\d+\s*股", seg):
                violations_r13.append(code)
    checks["R13_whitelist"] = "✅" if not violations_r13 else f"❌ {violations_r13}"
    # 3. R11 信号一票否决（简化检测：报告含"看空"+"加仓"在 100 字内 → 嫌疑）
    suspicious = []
    for m in re.finditer(r"看空|bearish", text):
        window = text[m.start():m.start()+200]
        if re.search(r"加仓|买入|追高|介入", window):
            suspicious.append(text[max(0,m.start()-20):m.start()+100])
    checks["R11_signal_veto"] = "✅" if not suspicious else f"⚠️ {len(suspicious)} 处嫌疑"
    # 4. 数据源链可追溯
    has_data_src = any(k in text for k in ["TOOLS.md", "signals_summary", "settlement.json", "focus_watchlist"])
    checks["data_source_traceable"] = "✅" if has_data_src else "⚠️ 未引用任何权威数据源"
    # 5. 规则索引可追溯
    has_rule_ref = bool(re.search(r"《.{2,15}规则》|R\d+", text))
    checks["rule_reference"] = "✅" if has_rule_ref else "⚠️ 未引用任何规则编号"
    return checks


def main():
    holdings = parse_holdings_from_tools()
    cleared = parse_cleared_from_tools()
    focus = load_focus_watchlist()

    # SOP-M3 一致性
    holding_codes = {h["code"] for h in holdings}
    focus_hold_codes = {f.get("code") for f in focus if f.get("hold")} if focus else set()
    inconsistent_focus = sorted(focus_hold_codes - holding_codes)

    # 待审计报告：今日所有 + 本周 weekly
    targets = []
    today_dir = REPORTS_DIR
    if today_dir.exists():
        for f in today_dir.glob(f"{TODAY}-*.md"):
            targets.append(f)
        weekly_dir = REPORTS_DIR / "weekly"
        if weekly_dir.exists():
            for f in sorted(weekly_dir.glob("*.md"))[-1:]:
                targets.append(f)

    results = {
        "audit_time": datetime.now().isoformat(),
        "data_source_check": {
            "holdings_in_tools_md": len(holdings),
            "cleared_in_tools_md": len(cleared),
            "focus_hold_count": len(focus_hold_codes),
            "inconsistent_focus_hold": inconsistent_focus,
            "consistency": "✅" if not inconsistent_focus else f"❌ {len(inconsistent_focus)} 只 focus.hold 与 TOOLS.md 持仓不一致",
        },
        "reports_audit": [check_report(p, holdings, cleared) for p in targets],
        "summary": {
            "total_reports": len(targets),
            "with_r16_self_check": sum(1 for p in targets if check_report(p, holdings, cleared).get("R16_self_check") == "✅"),
        }
    }

    # 计算违规计数
    violations = []
    for r in results["reports_audit"]:
        if not r.get("exists"):
            continue
        if r.get("R16_self_check") == "❌":
            violations.append(f"{r['path']} 缺 R16 自检")
        if r.get("R13_whitelist", "").startswith("❌"):
            violations.append(f"{r['path']} R13 违规：{r['R13_whitelist']}")
        if r.get("R11_signal_veto", "").startswith("⚠️"):
            violations.append(f"{r['path']} R11 嫌疑：{r['R11_signal_veto']}")
    results["violations"] = violations
    results["health"] = "🟢 健康" if not violations and not inconsistent_focus else f"🟡 发现 {len(violations) + (1 if inconsistent_focus else 0)} 项问题"

    # 输出
    out_dir = WORKSPACE / "data"
    out_dir.mkdir(exist_ok=True)
    out_file = out_dir / "rules_audit_latest.json"
    out_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"=== 规则执行体检 · {TODAY} ===")
    print(f"持仓数: {len(holdings)} | 已清仓: {len(cleared)} | focus.hold: {len(focus_hold_codes)}")
    print(f"一致性: {results['data_source_check']['consistency']}")
    print(f"待审报告: {len(targets)}")
    for r in results["reports_audit"]:
        if not r.get("exists"):
            continue
        print(f"\n📄 {r['path']}")
        for k in ["R16_self_check", "R13_whitelist", "R11_signal_veto", "data_source_traceable", "rule_reference"]:
            print(f"   {k}: {r.get(k)}")
    print(f"\n=== 健康度: {results['health']} ===")
    if violations:
        print("违规清单:")
        for v in violations:
            print(f"  - {v}")
    print(f"\n报告写入: {out_file}")


if __name__ == "__main__":
    main()
