#!/usr/bin/env python3
import sys
"""
Top5 精选 — 基于引擎信号的加权排序 v4.0 (2026-06-04)
==============================================
不再自算EMA/MACD/形态，从 engine_signals.json 读取评分数据做加权排序
用法: python3 scripts/top5.py
"""
import json, re, os
from datetime import datetime

ENGINE_PATH = "/tmp/stock_alerts/engine_signals.json"
SIGNALS_SUMMARY = "/tmp/stock_alerts/signals_summary.json"
WORKSPACE = "/root/.openclaw/workspace"


def load_resonance_summary():
    """从 signals_summary.json 加载共振、形态、紧急信号"""
    result = {"resonance": {}, "morphology": {}, "urgent": {}, "stop": {}}
    if not os.path.exists(SIGNALS_SUMMARY):
        return result
    try:
        with open(SIGNALS_SUMMARY) as f:
            data = json.load(f)

        # 共振方向分级
        for level, direction in [
            ('buy', 2), ('observe', 1), ('observe_weak', 0.5),
            ('warn', 0), ('single', 0), ('sell', -1)
        ]:
            for item in data.get('resonance', {}).get(level, []):
                m = re.match(r'.+?\((\d+)\)', item)
                if m:
                    code = m.group(1)
                    if code not in result["resonance"] or abs(direction) > abs(result["resonance"][code]):
                        result["resonance"][code] = direction

        # 形态信号
        for morph_name, items in data.get('morphology', {}).items():
            for item in items:
                m = re.match(r'.+?\((\d+)\)', item)
                if m:
                    code = m.group(1)
                    if code not in result["morphology"]:
                        result["morphology"][code] = []
                    result["morphology"][code].append(morph_name)
    except:
        pass
    return result


# ========== 从引擎加载全量评分 ==========
def load_engine_scores():
    """加载 engine_signals.json，提取引擎评分+信号方向+共振"""
    if not os.path.exists(ENGINE_PATH):
        print(f"❌ 引擎数据文件不存在: {ENGINE_PATH}", file=sys.stderr)
        return {}
    with open(ENGINE_PATH) as f:
        data = json.load(f)

    scores = {}
    for item in data:
        code = item.get('code', '')
        if not code:
            continue

        name = item.get('name', '')
        price = item.get('price', 0)
        change = item.get('change_pct', 0)

        # 引擎核心分数（直接从 engine_signals 读取）
        quality_score = item.get('quality_score', 0) or 0
        morph_score = item.get('morph_score', 0) or 0  # 引擎22种形态评分
        total_ext = item.get('total_score_ext', 0) or 0  # 引擎综合分

        # 信号方向加权
        signal_direction_sum = 0.0
        signal_items = []
        for s in item.get('signals', []):
            direction = s.get('direction', '')
            strength = s.get('strength', 'medium')
            rule = s.get('rule', '')

            # 方向分值
            dmap = {
                'bullish': 1, 'bullish_urgent': 1, 'breakout': 1, 'up': 1,
                'strong_hold': 1, 'buy_signal': 1, 'active': 1,
                'bullish_context': 0.5,
                'bearish': -1, 'bearish_warn': -1, 'sell_signal': -1, 'breakdown': -1,
                'reversal_warn': -0.5,
                'no_add': -0.3, 'risk_mgmt': -0.3, 'exclude_buy': -0.3,
            }
            sv = dmap.get(direction, 0)

            # 强度权重
            sw = {'very_high': 2.0, 'high': 1.5, 'medium': 1.0, 'low': 0.5}.get(strength, 1.0)

            if sv != 0:
                signal_direction_sum += sv * sw
                signal_items.append(f"{direction}({rule})")

        # 共振数据
        res = item.get('resonance', {})
        buy_n = res.get('buy_signals', 0)
        sell_n = res.get('sell_signals', 0)
        verdict = res.get('verdict', '')

        # 共振分值：优先引擎verdict等级
        resonance_score = 0.0
        if buy_n > 0 and sell_n == 0:
            resonance_score = min(buy_n * 0.3, 0.8)
        elif sell_n > 0 and buy_n == 0:
            resonance_score = -min(sell_n * 0.4, 0.8)
        elif buy_n > 0 and sell_n > 0:
            net = buy_n * 0.3 - sell_n * 0.4
            resonance_score = net * 0.5  # 冲突减半

        # 多空冲突标记
        has_conflict = buy_n >= 1 and sell_n >= 1

        # price_level
        price_level = item.get('price_level', '')

        scores[code] = {
            "name": name,
            "price": price,
            "change": change,
            "quality": quality_score,
            "morph_engine": morph_score,
            "total_ext": total_ext,
            "signal_direction": signal_direction_sum,
            "signal_items": signal_items,
            "resonance_score": resonance_score,
            "resonance_verdict": verdict,
            "buy_n": buy_n,
            "sell_n": sell_n,
            "has_conflict": has_conflict,
            "price_level": price_level,
        }

    return scores


# ========== 引擎加权排序计算 ==========
def compute_engine_rank(code, engine_data, summary):
    """基于引擎数据做加权排序，返回 (综合分, 形态分, 拆解字符串)"""
    ed = engine_data.get(code)
    if not ed:
        return 0, 0, "引擎无数据"

    parts = []

    # 1️⃣ 引擎综合分 (0~5) → 归一化到 0~1.5
    total_ext = ed["total_ext"]
    ext_score = min(total_ext / 5.0, 1.5)
    parts.append(f"引擎分:{ext_score:.2f}")

    # 2️⃣ 信号方向分 (-3~+3) → 归一化到 -0.5~+1.0
    sig_dir = ed["signal_direction"]
    sig_score = max(min(sig_dir * 0.3, 1.0), -0.5)
    if sig_score > 0:
        parts.append(f"信号:{sig_score:.2f}")
    elif sig_score < 0:
        parts.append(f"信号:{sig_score:.2f}")

    # 3️⃣ 共振分 (-0.8~+0.8) → 直接加分
    res_score = ed["resonance_score"]
    if res_score != 0:
        parts.append(f"共振:{res_score:+.2f}")

    # 4️⃣ 多空冲突降级
    conflict_penalty = -0.2 if ed["has_conflict"] and ed["buy_n"] >= 2 and ed["sell_n"] >= 1 else 0
    if conflict_penalty < 0:
        parts.append(f"冲突:{conflict_penalty:.2f}")

    # 5️⃣ 形态分数来源 + 展示
    morph_e = ed["morph_engine"]
    morph_label = f"形态:{morph_e:.2f}"

    # 从summary补充形态名
    summary_morph = summary.get("morphology", {}).get(code, [])
    if summary_morph:
        morph_label += f"({'|'.join(summary_morph[:3])})"

    # 6️⃣ 共振等级展示
    res_verdict = ed["resonance_verdict"]
    rlev = summary.get("resonance", {}).get(code, 0)
    rlabel = {2: '三重共振↑', 1: '双重确认↑', 0.5: '单一信号↑', 0: '中性', -1: '卖出确认↓'}.get(rlev, '')
    if rlabel:
        parts.append(f"评级:{rlabel}")

    total = ext_score + sig_score + res_score + conflict_penalty
    morph_display = min(morph_e, 1.0)

    detail_str = '|'.join(parts)
    return total, morph_display, detail_str, ed


# ========== 主流程 ==========
def main():
    print("\n⏳ 加载引擎评分数据...", file=sys.stderr, end='')
    scores = load_engine_scores()
    if not scores:
        return
    print(f" ✅ {len(scores)}只标的", file=sys.stderr)

    print("⏳ 加载共振+形态摘要...", file=sys.stderr, end='')
    summary = load_resonance_summary()
    print(f" ✅ 共振{len(summary['resonance'])}只, 形态{len(summary['morphology'])}只", file=sys.stderr)

    print("⏳ 引擎加权排序中...", file=sys.stderr, end='')
    results = []
    for code, ed in scores.items():
        total, morph, detail, raw = compute_engine_rank(code, scores, summary)
        results.append((total, code, ed["name"], ed["price"], ed["change"], morph, detail, raw))
    print(" ✅", file=sys.stderr)

    results.sort(key=lambda x: x[0], reverse=True)

    print()
    print('══════════════════════════════════════════════')
    print('🏆  TOP5 精选 — 引擎加权排序 v4.0')
    print('══════════════════════════════════════════════')
    print()

    for i, (total, code, name, price, change, morph, detail, raw) in enumerate(results[:5], 1):
        chg_f = float(change or 0)
        arrow = '🟢' if chg_f >= 0 else '🔴'
        chg_s = f'+{chg_f}%' if chg_f >= 0 else f'{chg_f}%'
        print(f'  #{i}  {name} ({code})  {arrow} {chg_s}')
        me = raw['morph_engine']
        te = raw['total_ext']
        print('      价' + str(price) + '  综合分' + '{:.2f}'.format(total) + ' = 形态' + '{:.2f}'.format(me) + '+引擎' + '{:.2f}'.format(te) + '\n')
        signal_str = '|'.join(raw['signal_items'][:5])
        print('      ' + signal_str + '\n')
        print(f'      引擎评分明细: {detail}')
        if raw["has_conflict"]:
            if raw['has_conflict']:
                print('      ⚠️ 多空冲突: 看多' + str(raw['buy_n']) + '个 vs 看空' + str(raw['sell_n']) + '个')
        rlev = summary.get("resonance", {}).get(code, 0)
        if rlev != 0:
            rlabel = {2: '三重共振', 1: '双重确认', 0.5: '单一信号看多', -1: '卖出确认'}.get(rlev, str(rlev))
            print(f'      📡 信号引擎共振: {rlabel}')
        morph_list = summary.get("morphology", {}).get(code, [])
        if morph_list:
            print(f'      🎯 信号引擎形态: {", ".join(morph_list[:4])}')
        print()

    # 完整排名
    print('📋 完整评分排序（前15）:')
    print()
    for i, (total, code, name, price, change, morph, detail, raw) in enumerate(results[:15], 1):
        arrow = '🟢' if float(change or 0) >= 0 else '🔴'
        chg = f'{change}' if float(change or 0) < 0 else f'+{change}'
        row_te = raw['total_ext']
        row_me = raw['morph_engine']
        print('  {0:2d}. {1:<10s}({2:<6s}) {3}{4:>7s}  总分{5:5.2f}  引擎{6:5.2f}  形态{7:.2f}'.format(i, name, code, arrow, chg, total, row_te, row_me))

    # 形态排名
    morph_list = [(t, c, n, p) for t, c, n, p, ch, m, d, r in results if r["morph_engine"] >= 0.5]
    if morph_list:
        print(f'\n🎯 引擎形态高评分({len(morph_list)})')
        for _, c, n, p in morph_list[:15]:
            print(f'  {n}({c}) ¥{p}')

    # 分布统计
    bins = {'3.0+': 0, '2.0-2.9': 0, '1.0-1.9': 0, '<1.0': 0}
    for total, *_ in results:
        if total >= 3.0: bins['3.0+'] += 1
        elif total >= 2.0: bins['2.0-2.9'] += 1
        elif total >= 1.0: bins['1.0-1.9'] += 1
        else: bins['<1.0'] += 1
    parts2 = [f'{k}: {v}' for k, v in bins.items() if v > 0]
    print(f'\n📊 共{len(results)}只入库 | {" | ".join(parts2)}')
    print(f'\n⏱ {datetime.now().strftime("%Y-%m-%d %H:%M")}')


if __name__ == '__main__':
    main()
