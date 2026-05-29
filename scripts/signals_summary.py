#!/usr/bin/env python3
"""
信号引擎摘要生成器 - 将189KB的engine_signals.json压缩为结构化摘要
保留所有核心信息：共振判定/形态信号/评分排行/紧急信号/止损告警
输出到 /tmp/stock_alerts/signals_summary.json (目标3-5KB)
"""
import json, sys, os

INPUT = "/tmp/stock_alerts/engine_signals.json"
OUTPUT = "/tmp/stock_alerts/signals_summary.json"

def main():
    if not os.path.exists(INPUT):
        print(f"错误: {INPUT} 不存在")
        sys.exit(1)

    with open(INPUT) as f:
        data = json.load(f)

    resonance = {"buy": [], "observe": [], "sell": [], "warn": []}
    morph = {}
    score_ranking = []
    urgent = []
    stops = []

    for item in data:
        code = item.get("code","")
        name = item.get("name","")
        price = item.get("price", 0)
        try:
            pct = float(item.get("change_pct","0"))
        except:
            pct = 0
        level = item.get("price_level","")
        ts = item.get("total_score_ext", 0)
        ms = item.get("morph_score", 0)
        res = item.get("resonance", {})
        v = res.get("verdict","")
        buy_n = res.get("buy_signals", 0)
        sell_n = res.get("sell_signals", 0)
        signals = item.get("signals", [])

        # 简洁条目格式
        e = f"{name}({code}) ¥{price:.2f} {pct:+.1f}%"

        # 共振分组
        if "三重共振" in v:
            resonance["buy"].append(e)
        elif "双重确认" in v:
            resonance["observe"].append(e)
        elif "卖出确认" in v:
            resonance["sell"].append(e)
        elif "卖出预警" in v:
            resonance["warn"].append(e)

        # 评分TOP15
        score_ranking.append((ts, ms, e, v[:6]))

        # 紧急信号
        if level == "L3_URGENT":
            top_note = signals[0].get("note","") if signals else ""
            urgent.append(f"{e} | {v} | {top_note}")

        # 形态信号
        for sig in signals:
            rule = sig.get("rule","")
            direction = sig.get("direction","")
            strength = sig.get("strength","")

            tag = None
            if rule == "red_three": tag = "红三兵"
            elif rule == "breakout_up": tag = "放量突破"
            elif rule in ("volume_shrink","vol_down_shrink") and strength in ("high","very_high"): tag = "缩量洗盘"
            elif rule == "bullish_arrangement": tag = "多头排列"
            elif rule == "bearish_arrangement": tag = "空头排列"
            elif rule == "ma_golden_cross": tag = "均线金叉"
            elif rule == "ma_death_cross": tag = "均线死叉"
            elif rule == "macd_bottom_div": tag = "MACD底背离"
            elif rule == "macd_top_div": tag = "MACD顶背离"
            elif rule == "rsi_oversold": tag = "RSI超卖"
            elif rule == "rsi_overbought": tag = "RSI超买"
            elif rule == "limit_down": tag = "跌停板"
            elif direction == "limit_up": tag = "涨停板"
            elif rule in ("gap_up","gap_down"): tag = "缺口"
            elif rule == "doji": tag = "十字星"
            elif rule == "hammer": tag = "锤子线"
            elif rule == "hanging_man": tag = "上吊线"
            elif rule == "shooting_star": tag = "射击之星"
            elif rule == "historical_breakthrough": tag = "前高突破"
            elif rule == "historical_resistance": tag = "前高压制"
            elif rule == "breakdown": tag = "跌破支撑"
            elif rule in ("ma_convergence_up","ma_convergence_down"): tag = "均线收敛"
            elif rule == "2b_fake_breakout": tag = "假突破"
            elif rule == "should_fall_strong": tag = "该跌不跌"
            elif rule == "shrink_then_breakout": tag = "缩量后放量突破"
            elif rule == "volume_pullback_support": tag = "回踩支撑缩量"
            elif rule == "upper_wick" and strength in ("high","very_high"): tag = "上影线警示"

            if tag:
                morph.setdefault(tag, []).append(e)

            # 止损告警
            if rule == "entry_stop_loss":
                stops.append(f"{e} | {sig.get('note','')}")
            elif rule == "trailing_stop_urgent":
                stops.append(f"🔴 {e} | 移动止盈止损触发")

    # --- 后处理 ---

    # 去重形态（每个标的每类只保留一次）
    for k in list(morph.keys()):
        seen = set()
        deduped = []
        for s in morph[k]:
            code_part = s.split("(")[1].split(")")[0] if "(" in s else s
            if code_part not in seen:
                seen.add(code_part)
                deduped.append(s)
        if len(deduped) > 6:
            deduped = deduped[:6]
        morph[k] = deduped

    # 评分TOP15
    score_ranking.sort(key=lambda x: -x[0])
    top_scored = [f"{e} | TS:{ts:.2f} MS:{ms:+.2f} | {v}" for ts,ms,e,v in score_ranking[:15]]

    # 卖出确认只保留TOP8最严重
    resonance["sell"] = resonance["sell"][:8]

    # 过滤空组
    resonance = {k: v for k, v in resonance.items() if v}
    morph = {k: v for k, v in morph.items() if v}
    # 紧急最多12条
    urgent = urgent[:12]

    # 输出
    summary = {
        "resonance": resonance,
        "morphology": morph,
        "top_scored": top_scored,
        "urgent": urgent,
        "stop_alerts": stops,
    }

    with open(OUTPUT, 'w') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    size = os.path.getsize(OUTPUT)
    orig = os.path.getsize(INPUT)
    print(f"✅ 摘要: {OUTPUT} ({size}B, 压缩率 {size/orig*100:.1f}%)")
    for k, v in resonance.items():
        print(f"   {k}: {len(v)}只")
    print(f"   形态信号: {len(morph)}种, {sum(len(v) for v in morph.values())}条")
    print(f"   TOP评分: {len(top_scored)} | 紧急: {len(urgent)} | 止损: {len(stops)}")

if __name__ == "__main__":
    main()
