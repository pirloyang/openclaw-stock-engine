#!/usr/bin/env python3
"""
概念板块相对强度分析 — 独立于引擎运行

两步:
  1. 从gtimg拉取概念成分股实时行情
  2. 计算个股vs板块超额收益 → 输出JSON信号到文件

用法: python3 concept_relative_strength.py [--append <引擎信号文件>]
"""

import sys, json, os, subprocess
from urllib.request import urlopen
import re

SIGNAL_DIR = os.path.dirname(os.path.abspath(__file__))
CONCEPT_MAP = os.path.join(SIGNAL_DIR, "concept_map.json")
BENCHMARK_FILE = "/tmp/concept_benchmarks.json"

def code_to_gtimg(code):
    """股票代码 → gtimg查询代码"""
    if code.startswith("6") or code.startswith("5") or code == "000001":
        return f"sh{code}"
    return f"sz{code}"

def fetch_gtimg(codes):
    """批量拉取实时行情"""
    batch_size = 50
    results = {}
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i+batch_size]
        qs = ",".join(code_to_gtimg(c) for c in batch)
        url = f"https://qt.gtimg.cn/q={qs}"
        try:
            resp = urlopen(url, timeout=10)
            raw = resp.read().decode("gbk")
            for line in raw.strip().split("\n"):
                parts = line.split("~")
                if len(parts) < 33:
                    continue
                code = parts[2]
                try:
                    change = float(parts[32].replace("%", "").strip())
                    price = parts[4]
                    name = parts[1]
                    results[code] = {"price": price, "change": change, "name": name}
                except (ValueError, IndexError):
                    pass
        except Exception:
            pass
    return results

def main():
    # 读取概念映射
    with open(CONCEPT_MAP) as f:
        cmap = json.load(f)
    
    concepts = cmap.get("concepts", {})
    stock_to_concepts = cmap.get("stock_to_concepts", {})
    
    # 收集所有概念成分股
    all_concept_codes = set()
    for cname, cdef in concepts.items():
        for s in cdef.get("sampled_stocks", []):
            all_concept_codes.add(s)
    
    # 排除不在监控池中的代码
    all_concept_codes = {c for c in all_concept_codes}
    
    if not all_concept_codes:
        print("无概念成分股")
        return
    
    # 获取实时行情
    stocks = fetch_gtimg(list(all_concept_codes))
    print(f"获取到 {len(stocks)}/{len(all_concept_codes)} 只概念成分股行情")
    
    # 计算概念基准（等权平均）
    benchmarks = {}
    for cname, cdef in concepts.items():
        sample = cdef.get("sampled_stocks", [])
        changes = [stocks[s]["change"] for s in sample if s in stocks]
        if changes:
            avg = round(sum(changes) / len(changes), 2)
            benchmarks[cname] = {
                "name": cdef.get("name", cname),
                "bk_code": cdef.get("bk_code"),
                "avg_change": avg,
                "stocks_used": len(changes),
                "stocks_total": len(sample)
            }
    
    # 保存基准
    with open(BENCHMARK_FILE, "w") as f:
        json.dump(benchmarks, f, ensure_ascii=False, indent=2)
    
    # 计算个股相对强度
    signals = {}
    for code, sdata in stocks.items():
        name = sdata["name"]
        ind_change = sdata["change"]
        if code not in stock_to_concepts:
            continue
        
        for concept in stock_to_concepts[code]:
            if concept not in benchmarks:
                continue
            bm = benchmarks[concept]
            bm_change = bm["avg_change"]
            bm_name = bm["name"]
            excess = round(ind_change - bm_change, 2)
            
            sig = None
            direction = ""
            strength = ""
            note = ""
            
            if bm_change > 1 and ind_change < -1.5:
                sig = "relative_weakness"
                direction = "sell_signal"
                strength = "very_high"
                note = f"🔴个股显著弱于板块:{name}跌{ind_change}%但[{bm_name}]涨{bm_change}%"
            elif excess > 3:
                sig = "relative_strength"
                direction = "buy_signal"
                strength = "high"
                note = f"🟢个股强势于板块:{name}涨{ind_change}% 超[{bm_name}]({bm_change}%)达{excess}%"
            elif excess < -2.5:
                sig = "relative_weakness_moderate"
                direction = "sell_signal"
                strength = "high"
                note = f"⚠️个股弱于板块:{name}跑输[{bm_name}]{excess}%"
            elif excess > 2:
                sig = "relative_strength_moderate"
                direction = "buy_signal"
                strength = "medium"
                note = f"个股跑赢板块:{name}超[{bm_name}]{excess}%"
            
            if sig:
                if code not in signals:
                    signals[code] = []
                signals[code].append({
                    "rule": sig,
                    "direction": direction,
                    "excess": excess,
                    "concept": concept,
                    "concept_name": bm_name,
                    "concept_change": f"{bm_change}%",
                    "individual_change": f"{ind_change}%",
                    "strength": strength,
                    "note": note
                })
    
    # 输出摘要
    print(f"\n=== 概念板块基准 ===")
    for cname, bm in sorted(benchmarks.items()):
        print(f"  {bm['name']}: {bm['avg_change']}% ({bm['stocks_used']}/{bm['stocks_total']})")
    
    print(f"\n=== 相对强度信号 ({len(signals)} 个标的) ===")
    for code, sigs in sorted(signals.items()):
        name = stocks.get(code, {}).get("name", code)
        change = stocks.get(code, {}).get("change", 0)
        print(f"  {name}({code}): {change}%")
        for sig in sigs:
            print(f"    {sig['note']}")
    
    # 如果提供--append参数，追加到引擎信号文件
    if len(sys.argv) > 1 and sys.argv[1] == "--append" and len(sys.argv) > 2:
        sig_file = sys.argv[2]
        if os.path.exists(sig_file):
            with open(sig_file) as f:
                engine_data = json.load(f)
            
            engine_code_map = {s["code"]: s for s in engine_data}
            appended = 0
            for code, sigs in signals.items():
                if code in engine_code_map:
                    for sig in sigs:
                        engine_code_map[code].setdefault("signals", []).append(sig)
                        appended += 1
            
            if appended > 0:
                with open(sig_file, "w") as f:
                    json.dump(engine_data, f, ensure_ascii=False, indent=2)
                print(f"\n已追加 {appended} 个信号到引擎输出")
    

    # 保存到文件供监控系统使用
    SIGNALS_OUTPUT = "/tmp/concept_signals.json"
    output = {
        "benchmarks": {k: {"name": v["name"], "avg_change": v["avg_change"], "stocks_used": v["stocks_used"], "stocks_total": v["stocks_total"]} for k, v in benchmarks.items()},
        "stocks": {code: {"name": sdata.get("name",""), "price": sdata.get("price",0), "change": sdata.get("change",0)} for code, sdata in stocks.items()},
        "signals": {code: [{"rule": s["rule"], "direction": s["direction"], "excess": s["excess"], "concept": s["concept"], "concept_name": s["concept_name"], "concept_change": s["concept_change"], "individual_change": s["individual_change"], "strength": s["strength"], "note": s["note"]} for s in sigs] for code, sigs in signals.items()}
    }
    with open(SIGNALS_OUTPUT, 'w') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    return signals

if __name__ == "__main__":
    main()
