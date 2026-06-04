#!/usr/bin/env python3
"""
Top5 精选 — 辉哥交易体系加权 v5.0 (2026-06-04)
================================================
拆解 engine_signals 多维度评分，按辉哥权重重新排序
权重: 形态35% > 共振25% > 方向20% > 质量15% > 板块5%
"""
import json, re, os, sys, math
from datetime import datetime

ENGINE_PATH = "/tmp/stock_alerts/engine_signals.json"
SIGNALS_SUMMARY = "/tmp/stock_alerts/signals_summary.json"
SECTOR_HISTORY = "/root/.openclaw/workspace/stock-signals/sector_history.json"
TOOLS_MD = "/root/.openclaw/workspace/TOOLS.md"

# 排除: 指数/ETF
EXCLUDE = {'000001', '399001', '399006', '516640', '159667', '159858', '159928', '512400'}

# 方向→分值
DIR_MAP = {
    'bullish': 1.0, 'bullish_urgent': 1.5, 'breakout': 1.2, 'up': 0.8,
    'strong_hold': 0.6, 'buy_signal': 1.0, 'active': 0.5, 'bullish_context': 0.3,
    'bearish': -1.0, 'bearish_warn': -0.5, 'sell_signal': -1.5, 'breakdown': -1.2,
    'reversal_warn': -0.8, 'no_add': -0.2, 'risk_mgmt': -0.2, 'exclude_buy': -0.5,
    'heavy_vol': 0, 'washout': 0, 'light_vol': 0, 'neutral': 0, 'overshoot': 0,
}
ST_W = {'very_high': 2.0, 'high': 1.5, 'medium': 1.0, 'low': 0.5, 'info': 0.3}

# 板块映射
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
    # 今日清仓记录
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
    # 持仓中标注'清仓'且不含'误报'
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


def load_sector_str():
    m = {}
    if not os.path.exists(SECTOR_HISTORY):
        return m
    try:
        with open(SECTOR_HISTORY) as f:
            data = json.load(f)
        for s, recs in data.items():
            if recs:
                lv = recs[-1].get('level', 'C')
                m[s] = {'A': 0.5, 'B': 0.3, 'C': 0.15}.get(lv, 0)
    except:
        pass
    return m


def load_engine(cleared):
    if not os.path.exists(ENGINE_PATH):
        return {}
    with open(ENGINE_PATH) as f:
        raw = json.load(f)

    out = {}
    for item in raw:
        code = item.get('code', '')
        if not code or code in EXCLUDE or code in cleared:
            continue

        # 维度1: 质量分
        quality = item.get('quality_score', 0) or 0
        # 维度2: 形态分（22种）
        morph = item.get('morph_score', 0) or 0
        # 维度3: 信号方向
        sig_dir = 0.0
        sig_list = []
        for s in item.get('signals', []):
            dv = DIR_MAP.get(s.get('direction', ''), 0)
            if dv != 0:
                sw = ST_W.get(s.get('strength', 'medium'), 1.0)
                sig_dir += dv * sw
                if abs(dv) >= 0.8:
                    sig_list.append(dict(
                        d=s.get('direction'), r=s.get('rule'), st=s.get('strength'),
                        v=dv*sw, n=s.get('note','')[:40]
                    ))
        # 维度4: 共振
        res = item.get('resonance', {})
        bn = res.get('buy_signals', 0)
        sn = res.get('sell_signals', 0)
        if bn >= 3 and sn == 0:
            rgrade = 1.0
        elif bn >= 2 and sn == 0:
            rgrade = 0.7
        elif bn >= 1 and sn == 0:
            rgrade = 0.4
        elif bn >= 1 and sn >= 1:
            rgrade = 0.2
        elif sn >= 1 and bn == 0:
            rgrade = -0.5
        else:
            rgrade = 0

        # 标记
        flags = []
        for s in item.get('signals', []):
            if s.get('rule') in ('macd_golden_cross', 'ma_golden_cross'):
                if '金叉' not in flags: flags.append('金叉')
            if s.get('rule') == 'macd_death_cross':
                if '死叉确认' not in flags: flags.append('死叉确认')
            if s.get('rule') == 'macd_death_ongoing' and '金叉' not in flags and '死叉确认' not in flags:
                flags.append('死叉持续')
            if s.get('rule') == 'historical_breakthrough':
                flags.append('前高突破')

        sector = SECTOR_MAP.get(code, '')
        verdict = res.get('verdict', '')

        out[code] = dict(
            n=item.get('name', ''), p=item.get('price', 0), ch=item.get('change_pct', 0),
            q=quality, mh=morph, sd=sig_dir, sl=sig_list,
            rg=rgrade, bn=bn, sn=sn, vd=verdict, sc=sector, fl=flags
        )
    return out


def score_weighted(ed, ss):
    mn = min(ed['mh'], 1.0)
    rn = (ed['rg'] + 0.5) / 1.5
    rn = max(min(rn, 1.0), 0)
    sn = 1.0 / (1.0 + math.exp(-ed['sd']))
    qn = min(ed['q'] / 3.0, 1.0)
    sec = ss.get(ed['sc'], 0) / 0.5

    total = mn * 0.35 + rn * 0.25 + sn * 0.20 + qn * 0.15 + sec * 0.05
    return total * 5.0, dict(m=mn, r=rn, s=sn, q=qn, sc=sec)


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


# ===== main =====
def main():
    sys.stderr.write("\n  loading engine...")
    cleared = cleared_codes()
    scores = load_engine(cleared)
    sys.stderr.write(" %d stocks\n" % len(scores))

    sys.stderr.write("  loading sector+morph...")
    ss = load_sector_str()
    mmap = load_morph_names()
    sys.stderr.write(" sectors=%d morph=%d\n" % (len(ss), len(mmap)))

    sys.stderr.write("  ranking...")
    results = []
    for code, ed in scores.items():
        ts, bd = score_weighted(ed, ss)
        results.append((ts, code, ed, bd))
    results.sort(key=lambda x: x[0], reverse=True)
    sys.stderr.write(" done\n\n")

    print('=' * 50)
    print('  TOP5 精选 -- 信号引擎评价体系 v5.0')
    print('  权重: 形态35% > 共振25% > 方向20% > 质量15% > 板块5%')
    print('=' * 50)
    print()

    for i, (ts, code, ed, bd) in enumerate(results[:5], 1):
        chg = float(ed['ch'] or 0)
        arrow = '🟢' if chg >= 0 else '🔴'
        chg_s = ('+' + str(chg) + '%') if chg >= 0 else (str(chg) + '%')

        print('  #%d  %s (%s)  %s %s' % (i, ed['n'], code, arrow, chg_s))
        print('      Y%s  综合分%.2f(0~5)' % (str(ed['p']), ts))
        print('      分解: 形态%.2f | 共振%.2f | 方向%.2f | 质量%.2f | 板块%.2f'
              % (bd['m'], bd['r'], bd['s'], bd['q'], bd['sc']))

        tops = sorted(ed['sl'], key=lambda x: abs(x['v']), reverse=True)[:3]
        sig = ' · '.join(["%s(%s)" % (s['d'], s['r']) for s in tops])
        if sig:
            print('      核心信号: ' + sig)

        mnames = mmap.get(code, [])
        if mnames:
            print('      引擎形态: ' + ' · '.join(mnames[:4]))

        if ed['fl']:
            print('      特殊标记: ' + ' · '.join(ed['fl']))

        if ed['sc']:
            print('      板块: ' + ed['sc'])

        vds = {1.0: '三重共振↑', 0.7: '双重确认↑', 0.4: '单一信号↑',
               0.2: '多空冲突', -0.5: '卖出确认↓'}
        print('      共振: ' + vds.get(ed['rg'], '中性') + ' (buy=%d sell=%d)' % (ed['bn'], ed['sn']))
        print()

    print('  Full Top 15:')
    print()
    for i, (ts, code, ed, bd) in enumerate(results[:15], 1):
        chg = float(ed['ch'] or 0)
        chg_s = ('+' + str(chg) + '%') if chg >= 0 else (str(chg) + '%')
        arrow = '🟢' if chg >= 0 else '🔴'
        morphs = '·'.join([x[:2] for x in mmap.get(code, [])[:3]])
        flags = '·'.join(ed['fl'][:2])
        print('  %2d. %-10s(%-6s) %s%8s  %5.2f  形态%4.2f  %-12s %s'
              % (i, ed['n'], code, arrow, chg_s, ts, ed['mh'], morphs, flags))

    mt = [(ed['mh'], code, ed['n'], ed['p']) for _, code, ed, _ in results if ed['mh'] >= 0.5]
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
