#!/usr/bin/env python3
"""
Top5 精选 — 信号引擎评价体系 v5.1 (2026-06-12)
================================================
直接用引擎 total_score_ext 排名，不再自算权重
"""
import json, re, os, sys
from datetime import datetime

ENGINE_PATH = None  # 自动发现最新 stock_signals_*.json

def _find_latest_signals():
    import glob
    files = glob.glob('/tmp/stock_signals_*.json')
    if not files:
        return '/tmp/stock_alerts/engine_signals.json'
    return max(files, key=os.path.getmtime)
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
    signal_path = _find_latest_signals()
    if not os.path.exists(signal_path):
        sys.stderr.write(" ERROR: engine_signals.json not found\n")
        return

    with open(signal_path) as f:
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
    print('  TOP10 精选 -- 信号引擎评价体系 v6.0')
    print('  排名依据: 引擎 total_score_ext（State×Tier加权）')
    print('=' * 50)
    print()

    for i, (ts, code, item) in enumerate(results[:10], 1):
        name = item.get('name', '?')
        chg = float(item.get('change_pct', 0) or 0)
        arrow = '🟢' if chg >= 0 else '🔴'
        chg_s = ('+' + str(chg) + '%') if chg >= 0 else (str(chg) + '%')
        morph = item.get('morph_score', 0)
        buy_vote = item.get('buy_vote', 0)
        sell_vote = item.get('sell_vote', 0)
        market_state = item.get('market_state', '?')
        price = item.get('price', 0)
        verdict = item.get('resonance', {}).get('verdict', '')
        bn = item.get('resonance', {}).get('buy_count', 0)
        sn = item.get('resonance', {}).get('sell_count', 0)
        tier_info = item.get('resonance', {}).get('tier_summary', '')

        print('  #%d  %s (%s)  %s %s' % (i, name, code, arrow, chg_s))
        print('      Y%s  引擎分%.2f | State=%s' % (str(price), ts, market_state))
        print('      形态%.2f | 买权%.2f/卖权%.2f | 共振: buy=%d sell=%d' % (morph, buy_vote, sell_vote, bn, sn))
        print('      引擎判决: %s' % verdict)

        # 形态规则（从signals数组提取，优先展示具体形态名）
        sigs = item.get('signals', [])
        morph_only = []
        for s in sigs:
            r = s.get('rule', '')
            if r and not any(x in r for x in ['chip_', 'entry_', 'price_action', 'vol_', 'turnover_']):
                if r not in morph_only:
                    morph_only.append(r)
        if morph_only:
            morph_cn = {
                'bullish_arrangement': '多头排列', 'bearish_arrangement': '空头排列',
                'macd_golden_cross': 'MACD金叉', 'macd_death_cross': 'MACD死叉',
                'macd_death_converging': '死叉收敛', 'macd_death_ongoing': '死叉持续',
                'macd_above_zero': '零轴上方', 'macd_below_zero': '零轴下方',
                'red_three': '红三兵', 'hammer': '锤子线', 'shooting_star': '射击之星',
                'doji': '十字星', 'hanging_man': '吊颈线',
                'breakout_up': '放量突破', 'breakout_down': '放量跌破',
                'gap_up': '跳空高开', 'gap_down': '跳空低开',
                'shrink_then_breakout': '缩量后突破', 'shrink_reversal': '缩量反转',
                'historical_breakthrough': '前高突破', '2b_fake_breakdown': '2B假跌破',
                '2b_fake_breakout': '2B假突破', 'morning_star': '早晨之星',
                'fairy_guide': '仙人指路', 'should_rise_fail': '该涨不涨',
                'should_fall_strong': '该跌不跌', 'ma_golden_cross': '均线金叉',
                'ma_death_cross': '均线死叉', 'price_level_support': '支撑位',
                'price_level_resistance': '压力位',
            }
            display = [morph_cn.get(r, r) for r in morph_only[:5]]
            print('      形态信号: ' + ' · '.join(display))

        # 核心信号（取top3方向信号）
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

    print('  Full Top 20:')
    print()
    for i, (ts, code, item) in enumerate(results[:20], 1):
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
