#!/usr/bin/env python3
"""
Top5 精选 — 入库标的综合评分排名
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

# ---------- 获取标的代码 ----------
def get_all_codes():
    codes = set()
    # 持仓
    import subprocess
    r = subprocess.run(["bash", TOOLS_SH, "holdings"], capture_output=True, text=True, timeout=10)
    for line in r.stdout.strip().split('\n'):
        parts = line.strip().split()
        if parts: codes.add(parts[0])
    # 历史自选
    r = subprocess.run(["bash", TOOLS_SH, "history"], capture_output=True, text=True, timeout=10)
    for line in r.stdout.strip().split('\n'):
        parts = line.strip().split()
        if parts: codes.add(parts[0])
    # ETF + 概念 + 监控 + 商业航天 + 风口
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
    """一次拉取全部行情"""
    batch = []
    for c in codes:
        prefix = "sh" if c[0] == '6' or c == '000001' else "sz"
        batch.append(f"{prefix}{c}")
    url = f"https://qt.gtimg.cn/q={','.join(batch)}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=20).read()
    raw = raw.decode('gbk', errors='replace')
    # 拆分多行
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
            # vol 是手数（字段36格式: price/vol/amt）
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

# ---------- 评分 ----------
def compute_score(code, name, price, change, vol, cache):
    score = 0.0
    details = []
    morph = 0.0

    abs_chg = abs(float(change or 0))
    chg = float(change or 0)

    # 1️⃣ 涨幅因子 (0-1)
    if abs_chg <= 1:
        score += 1.0
        details.append("涨幅:1.0")
    elif abs_chg <= 3:
        score += 0.5
        details.append("涨幅:0.5")
    else:
        details.append("涨幅:0")

    if not cache:
        return score, morph, '|'.join(details)

    ma5 = ma_n(cache, 5)
    ma10 = ma_n(cache, 10)
    ma20 = ma_n(cache, 20)
    high20 = high_n(cache, 20)
    low20 = low_n(cache, 20)
    avg10v = avgvol_n(cache, 10)

    # 2️⃣ 量能因子 (0-1)
    ratio = 0
    if avg10v and avg10v > 0:
        ratio = vol / avg10v
    if ratio >= 1.5:
        score += 1.0
        details.append("量能:1.0")
    elif ratio >= 1.3:
        score += 0.5
        details.append("量能:0.5")
    else:
        details.append(f"量能:0")

    # 3️⃣ 位置因子 (0-1)
    pos_score = 0.0
    if ma20 and ma20 > 0:
        dist = abs((price - ma20) / ma20 * 100)
        if dist <= 5:
            pos_score = 0.5
        if high20 and price >= high20:
            pos_score += 0.5
            pos_score = min(pos_score, 1.0)
        if dist > 10:
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
            if dist_pct > 10:
                trend_score *= 0.5
    elif ma20 and price > ma20:
        trend_score = 0.5
    score += trend_score
    details.append(f"趋势:{trend_score:.2f}")

    # 5️⃣ 盘前缓冲因子：量能不可用（缩量）时，用均线排列+MACD补充
    buffer_score = 0.0
    # 检查最近一次量能是否明显
    if ratio < 1.3:
        # A: 均线多头
        if ma5 and ma10 and ma20 and ma5 > ma10 > ma20:
            buffer_score += 0.5
        if ma5 and price > ma5:
            buffer_score += 0.25
        # MACD DIF > 0
        dif = calc_ema(cache['prices'], 12)
        if dif is not None:
            ema12 = dif
            ema26 = calc_ema(cache['prices'], 26)
            if ema26 is not None and ema12 - ema26 > 0:
                buffer_score += 0.25
        if buffer_score > 0:
            details.append(f"缓:{buffer_score:.2f}")
            score += buffer_score

    # 形态因子（基于日线）
    p = cache['prices']
    if len(p) >= 5:
        # 红三兵：连续3根阳线（收涨）
        if p[-1] > p[-2] > p[-3] > p[-4]:
            morph += 0.5
        # 放量突破最后2日涨幅递增
        if len(p) >= 3:
            d1 = (p[-1] - p[-2]) / p[-2] * 100
            d2 = (p[-2] - p[-3]) / p[-3] * 100
            if d1 > 0 and d1 > d2:
                morph += 0.3
        # 缩量回踩不破
        v = cache['vols']
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

    print("⏳ 计算评分中...", file=sys.stderr, end='')
    results = []
    for code in codes:
        if code not in prices: continue
        info = prices[code]
        cache = read_cache(code)
        total, morph, details = compute_score(
            code, info['name'], info['price'], info['change'], info['vol'], cache
        )
        results.append((total, code, info['name'], info['price'], info['change'], morph, details))
    print(" ✅", file=sys.stderr)

    results.sort(key=lambda x: x[0], reverse=True)

    # ======== 输出 ========
    print()
    print('══════════════════════════════════════════════')
    print('🏆  TOP5 精选 — 入库标的综合评分排名')
    print('══════════════════════════════════════════════')
    print()

    for i, (total, code, name, price, change, morph, details) in enumerate(results[:5], 1):
        chg_f = float(change or 0)
        arrow = '🟢' if chg_f >= 0 else '🔴'
        chg_s = f'+{chg_f}%' if chg_f >= 0 else f'{chg_f}%'
        score_no_morph = total - morph
        print(f'  #{i}  {name} ({code})  {arrow} {chg_s}')
        print(f'      价{price}  综合分{total:.2f} = 四因子{score_no_morph:.2f}+形态{morph:.2f}')
        print(f'      评分明细: {details}')
        print()

    # 完整前15
    print('📋 完整评分排序（前15）:')
    print()
    for i, (total, code, name, price, change, morph, details) in enumerate(results[:15], 1):
        arrow = '🟢' if float(change or 0) >= 0 else '🔴'
        chg = f'{change}' if float(change or 0) < 0 else f'+{change}'
        print(f'  {i:2d}. {name:<10s}({code:<6s}) {arrow}{chg:>7s}  总分{total:5.2f}  价{price}')

    # 形态激活
    morph_active = [(n, c, m) for t, c, n, p, ch, m, d in results if m >= 0.5]
    if morph_active:
        print(f'\n🎯 形态激活({len(morph_active)})')
        for name, code, ms in morph_active[:15]:
            print(f'  {name}({code}): 形态{ms:.2f}')
        if len(morph_active) > 15:
            print(f'  ...共{len(morph_active)}只')

    # 分布
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
