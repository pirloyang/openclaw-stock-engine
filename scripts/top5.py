#!/usr/bin/env python3
"""
Top5 精选 — 入库标的综合评分排名 v2.0
v2.1 (2026-06-04): 板块均值替代个股TS/共振方向修正/形态量价约束/技术指标独立
直接从 gtimg 行情 + 日线缓存独立评分，不依赖 engine.sh
用法: python3 scripts/top5.py
"""
import json, re, os, sys
from datetime import datetime
from collections import Counter
import urllib.request

WORKSPACE = "/root/.openclaw/workspace"
CACHE_DIR = os.path.join(WORKSPACE, "stock-signals", "cache")
TOOLS_SH = os.path.join(WORKSPACE, "scripts", "tools.sh")
SIGNALS_DIR = os.path.join(WORKSPACE, "stock-signals")
SIGNALS_SUMMARY = "/tmp/stock_alerts/signals_summary.json"
FOCUS_FILE = os.path.join(SIGNALS_DIR, "focus_watchlist.json")

# ---------- 获取标的代码 ----------
def get_all_codes():
    codes = set()
    import subprocess
    r = subprocess.run(["bash", TOOLS_SH, "holdings"], capture_output=True, text=True, timeout=10)
    for line in r.stdout.strip().split('\n'):
        parts = line.strip().split()
        if parts: codes.add(parts[0])
    r = subprocess.run(["bash", TOOLS_SH, "history"], capture_output=True, text=True, timeout=10)
    for line in r.stdout.strip().split('\n'):
        parts = line.strip().split()
        if parts: codes.add(parts[0])
    extras = """516640 159667 159858 159928 512400 688008 300308 300394 002230 300750
    300502 600522 300456 002281 300620 601138 000977 300476 000034 002837 300499
    301018 300738 300383 001309 300475 002119 300302 300661 688798 300223 603881
    300857 000032 002335 600602 600118 002025 300045 688568 300762 600343 300455
    688523 301306 002465 600391 600592 301005 000901 002682 600151 000551 300265
    002361 003009 600345 002151 000969 600183 002916 002938 002384"""
    for c in extras.split():
        codes.add(c.strip())
    return sorted(codes - {''})

# ---------- gtimg API ----------
def fetch_prices(codes):
    batch = []
    for c in codes:
        prefix = "sh" if c[0] == '6' or c == '000001' else "sz"
        batch.append(f"{prefix}{c}")
    url = f"https://qt.gtimg.cn/q={','.join(batch)}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=20).read()
    raw = raw.decode('gbk', errors='replace')
    lines = re.sub(r'";v_', '";\nv_', raw).strip().split('\n')
    result = {}
    for line in lines:
        line = line.strip()
        if not line or '~' not in line: continue
        parts = line.split('~')
        if len(parts) < 45: continue
        code = parts[2].strip()
        try:
            name = parts[1].strip()
            price = float(parts[3].strip() or 0)
            change = parts[32].replace('%', '').strip() if len(parts) > 32 else '0'
            vol_field = parts[35].strip() if len(parts) > 35 else '0'
            if '/' in vol_field:
                vol = float(vol_field.split('/')[1]) if len(vol_field.split('/')) > 1 else 0
            else:
                vol = float(vol_field or 0)
        except (ValueError, IndexError):
            continue
        result[code] = {'name': name, 'price': price, 'change': change, 'vol': vol}
    return result

# ---------- 日线缓存 ----------
def read_cache(code):
    path = os.path.join(CACHE_DIR, f"{code}.day")
    if not os.path.exists(path):
        return None
    prices, vols = [], []
    with open(path) as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) >= 2:
                try:
                    prices.append(float(parts[0]))
                    vols.append(float(parts[1]))
                except ValueError:
                    continue
    if len(prices) < 5:
        return None
    return {'prices': prices, 'vols': vols}

def ma_n(cache, n):
    if not cache or len(cache['prices']) < n: return None
    return sum(cache['prices'][-n:]) / n

def avgvol_n(cache, n):
    if not cache or len(cache['vols']) < n: return None
    return sum(cache['vols'][-n:]) / n

def high_n(cache, n):
    if not cache: return None
    return max(cache['prices'][-n:])

def low_n(cache, n):
    if not cache: return None
    return min(cache['prices'][-n:])

def calc_ema(prices, n):
    if len(prices) < n: return None
    k = 2 / (n + 1)
    ema = prices[0]
    for p in prices[1:n]:
        ema = p * k + ema * (1 - k)
    return ema

# ---------- 板块映射 ----------
SECTOR_MAP = {
    '601600': '有色', '000960': '有色', '600549': '有色', '603993': '有色',
    '002428': '有色', '600961': '有色', '000969': '有色',
    '688008': '半导体', '300223': '半导体', '603893': '半导体', '002049': '半导体',
    '002185': '半导体', '600584': '半导体', '603986': '半导体', '688525': '半导体',
    '301308': '半导体', '300661': '半导体', '688798': '半导体', '002119': '半导体',
    '300308': 'CPO', '300394': 'CPO', '300502': 'CPO', '002281': 'CPO',
    '000988': 'CPO', '300620': 'CPO',
    '601138': 'AI算力', '000977': 'AI算力', '000938': 'AI算力', '002837': 'AI算力',
    '300499': 'AI算力', '301018': 'AI算力', '300738': 'AI算力', '300383': 'AI算力',
    '603881': 'AI算力', '000034': 'AI算力', '002230': 'AI算力',
    '300476': 'PCB', '002916': 'PCB', '002938': 'PCB', '002384': 'PCB',
    '600183': 'PCB', '002463': 'PCB', '603256': 'PCB',
    '002594': '新能源', '300750': '新能源', '300390': '新能源', '300450': '新能源',
    '002865': '新能源', '603799': '新能源',
    '600580': '机器人', '300124': '机器人', '002896': '机器人', '600835': '机器人',
    '600592': '机器人', '002553': '机器人', '300660': '机器人',
    '300058': 'AI应用', '002131': 'AI应用', '300364': 'AI应用', '301171': 'AI应用',
    '600118': '商业航天', '002025': '商业航天', '300045': '商业航天', '688568': '商业航天',
    '300762': '商业航天', '600343': '商业航天', '300455': '商业航天', '688523': '商业航天',
    '301306': '商业航天', '600391': '商业航天', '000901': '商业航天', '600151': '商业航天',
    '003009': '商业航天',
    '002465': '军工', '600879': '军工', '000547': '军工', '002413': '军工',
    '300102': '半导体', '603618': '电缆', '600487': '光纤', '601869': '光纤',
    '002195': '科技', '300113': '算力', '300442': '算力',
}

# ---------- 板块强度 ----------
def load_sector_strength():
    """板块强度 = 同板块所有标的 TS 均值归一化（以点代面→以面代点）"""
    # 第一步：从 top_scored 解析每个标的的 TS
    stock_ts = {}
    if os.path.exists(SIGNALS_SUMMARY):
        try:
            with open(SIGNALS_SUMMARY) as f:
                data = json.load(f)
            for item in data.get('top_scored', []):
                m = re.match(r'.+?\((\d+)\)', item)
                if not m: continue
                code = m.group(1)
                ts_match = re.search(r'TS:([\d.]+)', item)
                if ts_match:
                    stock_ts[code] = float(ts_match.group(1))
        except:
            pass
    # 第二步：按板块聚合，计算均值
    sector_scores = {}
    for code, ts in stock_ts.items():
        sector = SECTOR_MAP.get(code)
        if not sector: continue
        if sector not in sector_scores:
            sector_scores[sector] = []
        sector_scores[sector].append(ts)
    # 第三步：均值归一化到 0-1
    sector_map = {}
    for sector, scores in sector_scores.items():
        avg_ts = sum(scores) / len(scores)
        sector_map[sector] = min(avg_ts / 5.0, 1.0)
    return sector_map

# ---------- 信号共振 ----------
def load_resonance():
    """从 signals_summary.json 的 resonance 解析信号共振方向
    observe=+1(看多), warn=0(中性), sell=-1(看空)
    同一标的取最极端方向"""
    resonance = {}
    if os.path.exists(SIGNALS_SUMMARY):
        try:
            with open(SIGNALS_SUMMARY) as f:
                data = json.load(f)
            r = data.get('resonance', {})
            for level, direction in [('observe', 1), ('warn', 0), ('sell', -1)]:
                for item in r.get(level, []):
                    m = re.match(r'.+?\((\d+)\)', item)
                    if m:
                        code = m.group(1)
                        # 取绝对值最大的方向（sell=-1 比 observe=+1 更极端时覆盖）
                        if code not in resonance or abs(direction) > abs(resonance[code]):
                            resonance[code] = direction
        except:
            pass
    return resonance

# ---------- 评分 v2.0 ----------
def compute_score(code, name, price, change, vol, cache, sector_strength, resonance_data):
    score = 0.0
    details = []
    morph = 0.0

    abs_chg = abs(float(change or 0))
    chg = float(change or 0)

    # 1️⃣ 涨幅因子 v2.0 (0-1): 区间打分
    if 1.0 <= abs_chg <= 3.0:
        score += 1.0; details.append("涨幅:1.0")
    elif 3.0 < abs_chg <= 5.0:
        score += 0.7; details.append("涨幅:0.7")
    elif 5.0 < abs_chg <= 8.0:
        score += 0.3; details.append("涨幅:0.3")
    elif abs_chg < 1.0:
        score += 0.2; details.append("涨幅:0.2")
    else:
        score += 0.1; details.append("涨幅:0.1")

    if not cache:
        return score, morph, '|'.join(details)

    ma5 = ma_n(cache, 5)
    ma10 = ma_n(cache, 10)
    ma20 = ma_n(cache, 20)
    high20 = high_n(cache, 20)
    low20 = low_n(cache, 20)
    avg10v = avgvol_n(cache, 10)

    # 2️⃣ 量能因子 v2.0 (0-1): 缩量自适应
    ratio = 0
    if avg10v and avg10v > 0:
        ratio = vol / avg10v
    if ratio >= 1.5:
        score += 1.0; details.append("量能:1.0")
    elif ratio >= 1.3:
        score += 0.7; details.append("量能:0.7")
    elif ratio >= 1.0:
        score += 0.4; details.append("量能:0.4")
    elif ratio >= 0.8:
        score += 0.2; details.append("量能:0.2")
    else:
        if ma5 and ma10 and ma20 and price > ma20 and ma5 > ma10:
            score += 0.3; details.append("量能:0.3(缩量回踩)")
        else:
            details.append("量能:0")

    # 3️⃣ 位置因子 v2.0 (0-1): 距MA20合理区间
    pos_score = 0.0
    if ma20 and ma20 > 0:
        dist = (price - ma20) / ma20 * 100
        abs_dist = abs(dist)
        if abs_dist <= 5:
            pos_score = 0.5
        elif abs_dist <= 10:
            pos_score = 0.7
        elif abs_dist <= 15:
            pos_score = 0.4
        else:
            pos_score = 0.2
        if high20 and price >= high20:
            pos_score += 0.3
            pos_score = min(pos_score, 1.0)
        if dist < 0:
            pos_score *= 0.5
    score += pos_score
    details.append(f"位置:{pos_score:.2f}")

    # 4️⃣ 趋势因子 (0-1)
    trend_score = 0.0
    if ma20 and ma20 > 0 and len(cache['prices']) >= 25:
        prev_ma20_vals = cache['prices'][-25:-5]
        if prev_ma20_vals:
            prev_ma20 = sum(prev_ma20_vals) / len(prev_ma20_vals)
            slope = (ma20 - prev_ma20) / prev_ma20 * 100
            if slope > 0.3:
                trend_score = 1.0
            elif slope < -0.3:
                trend_score = 0.0
            else:
                trend_score = 0.5
            dist_pct = (price - ma20) / ma20 * 100
            if dist_pct > 15:
                trend_score *= 0.5
    elif ma20 and price > ma20:
        trend_score = 0.5
    score += trend_score
    details.append(f"趋势:{trend_score:.2f}")

    # 5️⃣ 技术指标因子 v2.1 (0-0.5): 取消缩量限制，独立评估技术状态
    tech_score = 0.0
    if ma5 and ma10 and ma20 and ma5 > ma10 > ma20:
        tech_score += 0.3  # 均线多头排列
    if ma5 and price > ma5:
        tech_score += 0.15  # 站上5日线
    dif = calc_ema(cache['prices'], 12)
    if dif is not None:
        ema12 = dif
        ema26 = calc_ema(cache['prices'], 26)
        dea = calc_ema(cache['prices'], 9)
        if ema26 is not None and ema12 - ema26 > 0:
            tech_score += 0.15  # MACD DIF>0 多头区域
    if tech_score > 0:
        details.append(f"技术:{tech_score:.2f}")
        score += tech_score

    # 6️⃣ 板块强度因子 v2.1 (0-0.5): 板块均值TS归一化
    sector = SECTOR_MAP.get(code)
    sector_bonus = 0.0
    strength = sector_strength.get(sector, 0) if sector else 0
    if strength > 0:
        if strength >= 0.7:
            sector_bonus = 0.5
        elif strength >= 0.4:
            sector_bonus = 0.3
        elif strength >= 0.1:
            sector_bonus = 0.15
    if sector_bonus > 0:
        score += sector_bonus
        details.append(f"板块:{sector_bonus:.2f}")

    # 7️⃣ 信号共振因子 v2.1 (-0.3~+0.5): 看多加/看空扣
    resonance_bonus = 0.0
    if code in resonance_data:
        r = resonance_data[code]
        if r == 1:       # observe: 看多共振
            resonance_bonus = 0.5
        elif r == 0:     # warn: 中性
            resonance_bonus = 0.1
        elif r == -1:    # sell: 看空共振
            resonance_bonus = -0.3
    if resonance_bonus != 0:
        score += resonance_bonus
        details.append(f"共振:{resonance_bonus:+.2f}")

    # 形态因子 v2.1 (0-1): 量价配合约束
    p = cache['prices']
    v = cache['vols']
    avg5v = sum(v[-5:]) / 5 if len(v) >= 5 else 0
    if len(p) >= 5 and len(v) >= 5:
        # 红三兵: 连续4阳 + 最后2日量>5日均量
        if p[-1] > p[-2] > p[-3] > p[-4]:
            if v[-1] > avg5v and v[-2] > avg5v:
                morph += 0.5
            else:
                morph += 0.2  # 缩量红三兵降级
        # 涨幅加速: 涨幅递增 + 量比>1
        if len(p) >= 3:
            d1 = (p[-1] - p[-2]) / p[-2] * 100
            d2 = (p[-2] - p[-3]) / p[-3] * 100
            if d1 > 0 and d1 > d2:
                if v[-1] > avg5v:
                    morph += 0.3
                else:
                    morph += 0.1
        # 缩量回踩: 连续3日缩量（形态本身含缩量，逻辑正确）
        if len(v) >= 3 and v[-1] < v[-2] < v[-3]:
            morph += 0.2

    morph = min(morph, 1.0)
    total = score + morph
    return total, morph, '|'.join(details)

# ---------- 主流程 ----------
def main():
    print("\n⏳ 获取行情数据中...", file=sys.stderr, end='')
    codes = get_all_codes()
    prices = fetch_prices(codes)
    if not prices:
        print(" ❌ 行情获取失败", file=sys.stderr)
        return
    print(f" ✅ {len(prices)}只标的", file=sys.stderr)

    print("⏳ 加载板块+共振数据...", file=sys.stderr, end='')
    sector_strength = load_sector_strength()
    resonance_data = load_resonance()
    print(f" ✅ 板块{len(sector_strength)}个, 共振{len(resonance_data)}只", file=sys.stderr)

    print("⏳ 计算评分中...", file=sys.stderr, end='')
    results = []
    for code in codes:
        if code not in prices: continue
        info = prices[code]
        cache = read_cache(code)
        total, morph, details = compute_score(
            code, info['name'], info['price'], info['change'], info['vol'],
            cache, sector_strength, resonance_data
        )
        results.append((total, code, info['name'], info['price'], info['change'], morph, details))
    print(" ✅", file=sys.stderr)

    results.sort(key=lambda x: x[0], reverse=True)

    print()
    print('══════════════════════════════════════════════')
    print('🏆  TOP5 精选 — 入库标的综合评分排名 v2.0')
    print('══════════════════════════════════════════════')
    print()

    for i, (total, code, name, price, change, morph, details) in enumerate(results[:5], 1):
        chg_f = float(change or 0)
        arrow = '🟢' if chg_f >= 0 else '🔴'
        chg_s = f'+{chg_f}%' if chg_f >= 0 else f'{chg_f}%'
        score_no_morph = total - morph
        print(f'  #{i}  {name} ({code})  {arrow} {chg_s}')
        print(f'      价{price}  综合分{total:.2f} = 因子{score_no_morph:.2f}+形态{morph:.2f}')
        print(f'      评分明细: {details}')
        print()

    print('📋 完整评分排序（前15）:')
    print()
    for i, (total, code, name, price, change, morph, details) in enumerate(results[:15], 1):
        arrow = '🟢' if float(change or 0) >= 0 else '🔴'
        chg = f'{change}' if float(change or 0) < 0 else f'+{change}'
        print(f'  {i:2d}. {name:<10s}({code:<6s}) {arrow}{chg:>7s}  总分{total:5.2f}  价{price}')

    morph_active = [(n, c, m) for t, c, n, p, ch, m, d in results if m >= 0.5]
    if morph_active:
        print(f'\n🎯 形态激活({len(morph_active)})')
        for name, code, ms in morph_active[:15]:
            print(f'  {name}({code}): 形态{ms:.2f}')

    bins = {'4.0+': 0, '3.0-3.9': 0, '2.0-2.9': 0, '1.0-1.9': 0, '<1.0': 0}
    for total, *_ in results:
        if total >= 4.0: bins['4.0+'] += 1
        elif total >= 3.0: bins['3.0-3.9'] += 1
        elif total >= 2.0: bins['2.0-2.9'] += 1
        elif total >= 1.0: bins['1.0-1.9'] += 1
        else: bins['<1.0'] += 1
    parts = [f'{k}: {v}' for k, v in bins.items() if v > 0]
    print(f'\n📊 共{len(results)}只入库 | {" | ".join(parts)}')
    print(f'\n⏱ {datetime.now().strftime("%Y-%m-%d %H:%M")}')

if __name__ == '__main__':
    main()
