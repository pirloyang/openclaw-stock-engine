#!/usr/bin/env python3
"""
Top5 精选 — 信号引擎评价体系 v5.1 (2026-06-12)
================================================
直接用引擎 total_score_ext 排名，不再自算权重
"""
import json, re, os, sys
from datetime import datetime

ENGINE_PATH = "/tmp/stock_alerts/engine_signals.json"
SIGNALS_SUMMARY = "/tmp/stock_alerts/signals_summary.json"
TOOLS_MD = "/root/.openclaw/workspace/TOOLS.md"

# 排除: 指数/ETF
EXCLUDE = {'000001', '399001', '399006', '516640', '159667', '159858', '159928', '512400'}

# 板块映射（仅展示用）
SECTOR_MAP = {
    '300308': 'CPO', '300394': 'CPO', '300502': 'CPO', '002281': 'CPO', '000988': 'CPO', '300620': 'CPO',
    '688008': '存储', '603986': '存储', '688525': '存储', '301308': '存储', '300223': '存储', '001309': '存储',
    '300302': '存储', '300857': '存储',
    '300476': 'PCB', '002916': 'PCB', '002938': 'PCB', '002384': 'PCB', '600183': 'PCB',
    '002463': 'PCB', '603256': 'PCB',
    '603893': '芯片', '002049': '芯片', '002185': '芯片', '600584': '芯片', '300661': '芯片',
    '688798': '芯片', '002119': '芯片', '300102': '芯片',
    '600118': '航天', '002025': '航天', '300045': '航天', '688568': '航天', '300762': '航天',
    '600343': '航天', '300455': '航天', '688523': '航天', '301306': '航天', '600391': '航天',
    '000901': '航天', '600151': '航天', '003009': '航天',
    '002594': '新能源', '300750': '新能源', '300390': '新能源', '300450': '新能源', '002865': '新能源', '603799': '新能源',
    '600580': '机器人', '300124': '机器人', '002896': '机器人', '600835': '机器人', '600592': '机器人',
    '002553': '机器人', '300660': '机器人', '300809': '机器人',
    '300058': 'AI', '002131': 'AI', '300364': 'AI', '301171': 'AI', '002230': 'AI',
    '002837': '液冷', '300499': '液冷', '301018': '液冷', '300738': '液冷', '300383': '液冷',
    '603881': '液冷', '000032': '液冷', '002335': '液冷',
    '002371': '设备', '688012': '设备', '603203': '设备',
    '601600': '有色', '000960': '有色', '600549': '有色', '603993': '有色', '002428': '有色',
    '600961': '有色', '000969': '有色',
    '601138': '算力', '000977': '算力', '000938': '算力', '000034': '算力',
    '002465': '军工', '600879': '军工', '000547': '军工', '002413': '军工',
    '603618': '电缆', '600487': '光纤', '601869': '光纤', '002195': '科技', '300113': '算力', '300442': '算力',
}


def cleared_codes():
    s = set()
    if not os.path.exists(TOOLS_MD):
        return s
    with open(TOOLS_MD) as f:
        content = f.read()
    in_c = False
    for line in content.split('\n'):
        if '今日清仓记录' in line:
            in_c = True; continue
        if in_c:
            if line.strip().startswith('##') or line.strip().startswith('### '):
                break
            m = re.search(r'[-–]\s*\S+\s+(\d{6})', line)
            if m:
                s.add(m.group(1))
    in_h = False
    for line in content.split('\n'):
        if line.strip().startswith('### 持仓') or line.strip() == '### 持仓':
            in_h = True; continue
        if in_h and (line.strip().startswith('###') or line.strip().startswith('---')):
            break
        if in_h and '清仓' in line and '误报' not in line:
            m = re.search(r'(\d{6})', line)
            if m:
                s.add(m.group(1))
    return s


def load_morph_names():
    mm = {}
    if not os.path.exists(SIGNALS_SUMMARY):
        return mm
    try:
        with open(SIGNALS_SUMMARY) as f:
            data = json.load(f)
        for mn, items in data.get('morphology', {}).items():
            for item in items:
                m = re.match(r'.+?\((\d+)\)', item)
                if m:
                    code = m.group(1)
                    mm.setdefault(code, []).append(mn)
    except:
        pass
    return mm


def main():
    sys.stderr.write("\n  loading engine...")
    cleared = cleared_codes()
    if not os.path.exists(ENGINE_PATH):
        sys.stderr.write(" ERROR: engine_signals.json not found\n")
        return

    with open(ENGINE_PATH) as f:
        raw = json.load(f)
    sys.stderr.write(" %d stocks\n" % len(raw))

    sys.stderr.write("  loading morph...")
    mmap = load_morph_names()
    sys.stderr.write(" morph=%d\n" % len(mmap))

    sys.stderr.write("  ranking...")
    # 直接用引擎 total_score_ext 排名
    results = []
    for item in raw:
        code = item.get('code', '')
        if not code or code in EXCLUDE or code in cleared:
            continue
        ts = item.get('total_score_ext', 0)
        results.append((ts, code, item))
    results.sort(key=lambda x: x[0], reverse=True)
    sys.stderr.write(" done\n\n")

    print('=' * 50)
    print('  TOP5 精选 -- 信号引擎评价体系 v5.1')
    print('  排名依据: 引擎 total_score_ext（引擎内部加权）')
    print('=' * 50)
    print()

    for i, (ts, code, item) in enumerate(results[:5], 1):
        name = item.get('name', '?')
        chg = float(item.get('change_pct', 0) or 0)
        arrow = '🟢' if chg >= 0 else '🔴'
        chg_s = ('+' + str(chg) + '%') if chg >= 0 else (str(chg) + '%')
        morph = item.get('morph_score', 0)
        quality = item.get('quality_score', 0)
        price = item.get('price', 0)
        verdict = item.get('resonance', {}).get('verdict', '')
        bn = item.get('resonance', {}).get('buy_signals', 0)
        sn = item.get('resonance', {}).get('sell_signals', 0)

        print('  #%d  %s (%s)  %s %s' % (i, name, code, arrow, chg_s))
        print('      Y%s  引擎分%.2f(0~5)' % (str(price), ts))
        print('      形态%.2f | 质量%.2f | 共振: buy=%d sell=%d' % (morph, quality, bn, sn))
        print('      引擎判决: %s' % verdict)

        # 核心信号（取top3方向信号）
        sigs = item.get('signals', [])
        top_sigs = sorted(sigs, key=lambda s: abs(s.get('strength', 'medium') != 'info'), reverse=True)[:3]
        sig_strs = []
        for s in top_sigs:
            r = s.get('rule', '')
            d = s.get('direction', '')
            n = s.get('note', '')[:30]
            sig_strs.append('%s(%s)' % (d, r))
        if sig_strs:
            print('      核心信号: ' + ' · '.join(sig_strs))

        mnames = mmap.get(code, [])
        if mnames:
            print('      引擎形态: ' + ' · '.join(mnames[:4]))

        # 标记
        flags = []
        for s in sigs:
            if s.get('rule') in ('macd_golden_cross', 'ma_golden_cross'):
                if '金叉' not in flags: flags.append('金叉')
            if s.get('rule') == 'macd_death_cross':
                if '死叉确认' not in flags: flags.append('死叉确认')
            if s.get('rule') == 'macd_death_ongoing' and '金叉' not in flags and '死叉确认' not in flags:
                flags.append('死叉持续')
            if s.get('rule') == 'historical_breakthrough':
                flags.append('前高突破')
        if flags:
            print('      标记: ' + ' · '.join(flags))

        sector = SECTOR_MAP.get(code, '')
        if sector:
            print('      板块: ' + sector)
        print()

    print('  Full Top 15:')
    print()
    for i, (ts, code, item) in enumerate(results[:15], 1):
        name = item.get('name', '?')
        chg = float(item.get('change_pct', 0) or 0)
        chg_s = ('+' + str(chg) + '%') if chg >= 0 else (str(chg) + '%')
        arrow = '🟢' if chg >= 0 else '🔴'
        morph = item.get('morph_score', 0)
        morphs = '·'.join([x[:2] for x in mmap.get(code, [])[:3]])
        verdict = item.get('resonance', {}).get('verdict', '')[:8]
        print('  %2d. %-10s(%-6s) %s%8s  %5.2f  形态%4.2f  %-12s %s'
              % (i, name, code, arrow, chg_s, ts, morph, morphs, verdict))

    mt = [(item.get('morph_score', 0), code, item.get('name', '?'), item.get('price', 0))
          for _, code, item in results if item.get('morph_score', 0) >= 0.5]
    if mt:
        print('\n  Engine morphs >=0.5 (%d):' % len(mt))
        for ms, c, n, pr in sorted(mt, key=lambda x: x[0], reverse=True)[:12]:
            print('    %s(%s) Y%s  morph=%.2f' % (n, c, str(pr), ms))

    bins = {'3.0+': 0, '2.0-2.9': 0, '1.0-1.9': 0, '<1.0': 0}
    for ts, *_ in results:
        if ts >= 3.0: bins['3.0+'] += 1
        elif ts >= 2.0: bins['2.0-2.9'] += 1
        elif ts >= 1.0: bins['1.0-1.9'] += 1
        else: bins['<1.0'] += 1
    print('\n  %d stocks | %s' % (len(results), ' | '.join('%s: %d' % (k, v) for k, v in bins.items() if v)))
    print('\n  %s' % datetime.now().strftime('%Y-%m-%d %H:%M'))


if __name__ == '__main__':
    main()
