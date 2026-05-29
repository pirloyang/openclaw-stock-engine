#!/usr/bin/env python3
"""将概念信号合并到引擎输出信号文件中。
用法: python3 merge_concept_signals.py <引擎信号文件> [输出文件]
  默认输出到 /tmp/latest_signals.json
"""
import sys, json, os

SIGNAL_DIR = os.path.dirname(os.path.abspath(__file__))
CONCEPT_SIGNAL_FILE = "/tmp/concept_signals.json"
DEFAULT_OUTPUT = "/tmp/latest_signals.json"

def merge(engine_file, output_file=None):
    if output_file is None:
        output_file = DEFAULT_OUTPUT
    
    with open(engine_file) as f:
        engine_data = json.load(f)
    
    engine_codes = {s.get("code", "") for s in engine_data}
    
    if not os.path.exists(CONCEPT_SIGNAL_FILE):
        # 无概念信号文件，直接复制
        with open(output_file, "w") as f:
            json.dump(engine_data, f, ensure_ascii=False, indent=2)
        print(f"无概念信号文件，直接复制 {len(engine_data)} 条")
        return
    
    with open(CONCEPT_SIGNAL_FILE) as f:
        concept_data = json.load(f)
    
    signals_by_code = concept_data.get("signals", {})
    stock_info = concept_data.get("stocks", {})
    
    merged = list(engine_data)
    appended = 0
    supplemented = 0
    
    for code, siglist in signals_by_code.items():
        if code in engine_codes:
            # 追加到现有条目（不覆盖已有规则）
            for s in merged:
                if s.get("code") == code:
                    existing_rules = {sig.get("rule", "") for sig in s.get("signals", [])}
                    for sig in siglist:
                        if sig.get("rule", "") not in existing_rules:
                            s.setdefault("signals", []).append(sig)
                            existing_rules.add(sig.get("rule", ""))
                            supplemented += 1
                    break
        else:
            # 新建条目
            info = stock_info.get(code, {})
            new_entry = {
                "code": code,
                "name": info.get("name", code),
                "price": info.get("price", 0),
                "change_pct": f"{info.get('change', 0)}%",
                "signals": siglist,
                "concepts": [sig.get("concept_name", "") for sig in siglist if sig.get("concept_name")]
            }
            merged.append(new_entry)
            appended += 1
    
    with open(output_file, "w") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    
    print(f"合并完成: 追加{appended}个新概念股, 补充{supplemented}个信号到现有条目, 总{len(merged)}条")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"用法: {sys.argv[0]} <引擎信号文件> [输出文件]")
        sys.exit(1)
    merge(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
