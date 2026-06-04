#!/usr/bin/env python3
"""
Top5 精选 — 入库标的综合评分排名 v2.0
v3.0 (2026-06-04): 波动率衰减/死叉扣分/多空冲突降权/短中期背离惩罚
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
SECTOR_HISTORY = os.path.join(SIGNALS_DIR, "sector_history.json")
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
# 代码→板块映射（对齐 sector_history.json 概念板块）
SECTOR_MAP = {
    # CPO/光通信
    '300308': 'CPO/光通信', '300394': 'CPO/光通信', '300502': 'CPO/光通信',
    '002281': 'CPO/光通信', '000988': 'CPO/光通信', '300620': 'CPO/光通信',
    # HBM/存储
    '688008': 'HBM/存储', '603986': 'HBM/存储', '688525': 'HBM/存储',
    '301308': 'HBM/存储', '300223': 'HBM/存储', '001309': 'HBM/存储',
    '300302': 'HBM/存储', '300857': 'HBM/存储',
    # PCB/覆铜板
    '300476': 'PCB/覆铜板', '002916': 'PCB/覆铜板', '002938': 'PCB/覆铜板',
    '002384': 'PCB/覆铜板', '600183': 'PCB/覆铜板', '002463': 'PCB/覆铜板',
    '603256': 'PCB/覆铜板',
    # 半导体/芯片
    '603893': '半导体/芯片', '002049': '半导体/芯片', '002185': '半导体/芯片',
    '600584': '半导体/芯片', '300661': '半导体/芯片', '688798': '半导体/芯片',
    '002119': '半导体/芯片', '300102': '半导体/芯片',
    # 商业航天
    '600118': '商业航天', '002025': '商业航天', '300045': '商业航天',
    '688568': '商业航天', '300762': '商业航天', '600343': '商业航天',
    '300455': '商业航天', '688523': '商业航天', '301306': '商业航天',
    '600391': '商业航天', '000901': '商业航天', '600151': '商业航天',
    '003009': '商业航天',
    # 新能源
    '002594': '新能源', '300750': '新能源', '300390': '新能源',
    '300450': '新能源', '002865': '新能源', '603799': '新能源',
    # 机器人/工业母机
    '600580': '机器人/工业母机', '300124': '机器人/工业母机',
    '002896': '机器人/工业母机', '600835': '机器人/工业母机',
    '600592': '机器人/工业母机', '002553': '机器人/工业母机',
    '300660': '机器人/工业母机', '300809': '机器人/工业母机',
    # AI应用/大模型
    '300058': 'AI应用/大模型', '002131': 'AI应用/大模型',
    '300364': 'AI应用/大模型', '301171': 'AI应用/大模型',
    '002230': 'AI应用/大模型',
    # 液冷/数据中心
    '002837': '液冷/数据中心', '300499': '液冷/数据中心',
    '301018': '液冷/数据中心', '300738': '液冷/数据中心',
    '300383': '液冷/数据中心', '603881': '液冷/数据中心',
    '000032': '液冷/数据中心', '002335': '液冷/数据中心',
    # 半导体设备/材料
    '002371': '半导体设备/材料', '688012': '半导体设备/材料',
    '603203': '半导体设备/材料',
    # 有色（独立板块，不在 sector_history.json 中）
    '601600': '有色', '000960': '有色', '600549': '有色', '603993': '有色',
    '002428': '有色', '600961': '有色', '000969': '有色',
    # AI算力
    '601138': 'AI算力', '000977': 'AI算力', '000938': 'AI算力',
    '000034': 'AI算力',
    # 军工
    '002465': '军工', '600879': '军工', '000547': '军工', '002413': '军工',
    # 其他
    '603618': '电缆', '600487': '光纤', '601869': '光纤',
    '002195': '科技', '300113': '算力', '300442': '算力',
}

# ---------- 板块强度 ----------
def load_sector_strength():
    """板块强度 = sector_fund_flow.py 产出的 sector_history.json
    与 signal engine 共用同一数据源，避免自行计算"""
    sector_map = {}
    if os.path.exists(SECTOR_HISTORY):
        try:
            with open(SECTOR_HISTORY) as f:
                data = json.load(f)
            for sector, records in data.items():
                if not records: continue
                latest = records[-1]  # 最新一天
                level = latest.get('level', 'C')
                avg_chg = latest.get('avg_chg', 0)
                # 级别映射: A→0.5 B→0.3 C→0.15 D/E→0
                if level == 'A':
                    sector_map[sector] = 0.5
                elif level == 'B':
                    sector_map[sector] = 0.3
                elif level == 'C':
                    sector_map[sector] = 0.15
                # D/E: 弱势/恐慌，不给板块分
        except:
            pass
    # 有色等不在 sector_history.json 的板块，用行情数据补算
    return sector_map

# ---------- 信号共振 ----------
def load_resonance():
    """从 signals_summary.json 的 resonance 解析信号共振方向
    buy=+2(三重共振), observe=+1(双重确认), observe_weak=+0.5(单一信号看多)
    warn=0(中性), single=0(观望), sell=-1(卖出确认)
    同一标的取绝对值最大的方向"""
    resonance = {}
    if os.path.exists(SIGNALS_SUMMARY):
        try:
            with open(SIGNALS_SUMMARY) as f:
                data = json.load(f)
            r = data.get('resonance', {})
            for level, direction in [
                ('buy', 2), ('observe', 1), ('observe_weak', 0.5),
                ('warn', 0), ('single', 0), ('sell', -1)
            ]:
                for item in r.get(level, []):
                    m = re.match(r'.+?\((\d+)\)', item)
                    if m:
                        code = m.group(1)
                        if code not in resonance or abs(direction) > abs(resonance[code]):
                            resonance[code] = direction
        except:
            pass
    return resonance


# ---------- 共振冲突检测 ----------
def check_resonance_conflict(code):
    """检测engine_signals中是否存在多空信号冲突(buy≥1且sell≥1)"""
    engine_path = "/tmp/stock_alerts/engine_signals.json"
    if not os.path.exists(engine_path):
        return False
    try:
        with open(engine_path) as f:
            data = json.load(f)
        for item in data:
            if item.get('code', '') == code:
                r = item.get('resonance', {})
                buy_n = r.get('buy_signals', 0)
                sell_n = r.get('sell_signals', 0)
                return buy_n >= 1 and sell_n >= 1
    except:
        pass
    return False

# ---------- 评分 v3.0 ----------
def compute_score(code, name, price, change, vol, cache, sector_strength, resonance_data):
    score = 0.0
    details = []
    morph = 0.0

    abs_chg = abs(float(change or 0))
    chg = float(change or 0)

    # 1️⃣ 涨幅因子 v3.0 (0-1): 区间打分 + 波动率衰减
    chg_score = 0.0
    if 1.0 <= abs_chg <= 3.0:
        chg_score = 1.0
    elif 3.0 < abs_chg <= 5.0:
        chg_score = 0.7
    elif 5.0 < abs_chg <= 8.0:
        chg_score = 0.3
    elif abs_chg < 1.0:
        chg_score = 0.2
    else:
        chg_score = 0.1
    details.append(f"涨幅:{chg_score:.2f}")
    score += chg_score

    if not cache:
        return score, morph, '|'.join(details)

    # 波动率衰减: 5日内有过-5%以上单日暴跌 → 涨幅因子×0.5
    p_for_vol = cache['prices'] if cache else []
    if len(p_for_vol) >= 6:
        recent_5 = p_for_vol[-6:-1]  # day-5 ~ day-1
        for i in range(1, len(recent_5)):
            daily_chg = (recent_5[i] - recent_5[i-1]) / recent_5[i-1] * 100
            if daily_chg <= -5.0:
                score -= chg_score * 0.5  # 涨幅得分减半
                details[-1] = f"涨幅:{chg_score*0.5:.2f}(衰减)"
                break

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

    # 4️⃣ 趋势因子 v3.0 (0-1): MA20斜率 + 短中期背离惩罚
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
            # 短中期背离惩罚: MA20向上但价格<MA5且MACD死叉 → 中期趋势滞后
            if trend_score > 0.5 and ma5 and price < ma5:
                # 进一步判定MACD是否死叉
                ema12_t = calc_ema(cache['prices'], 12)
                ema26_t = calc_ema(cache['prices'], 26)
                if ema12_t and ema26_t:
                    dif_t = ema12_t - ema26_t
                    if dif_t > 0:
                        p_t = cache['prices']
                        e12v, e26v = [], []
                        for i, pr in enumerate(p_t):
                            if i == 0: e12v.append(pr); e26v.append(pr)
                            else: e12v.append(pr*2/13+e12v[-1]*(1-2/13)); e26v.append(pr*2/27+e26v[-1]*(1-2/27))
                        dif_v = [a-b for a,b in zip(e12v, e26v)]
                        dea_v = []
                        for i, d in enumerate(dif_v):
                            if i == 0: dea_v.append(d)
                            else: dea_v.append(d*2/10+dea_v[-1]*(1-2/10))
                        if dif_t <= dea_v[-1]:
                            trend_score = 0.5  # 死叉+价格<MA5→趋势降级
    elif ma20 and price > ma20:
        trend_score = 0.5
    score += trend_score
    details.append(f"趋势:{trend_score:.2f}")

    # 5️⃣ 技术指标因子 v3.0 (-0.35~+0.5): 加分+扣分双向
    tech_score = 0.0
    tech_cost = 0.0
    macd_dead = False
    if ma5 and ma10 and ma20 and ma5 > ma10 > ma20:
        tech_score += 0.3  # 均线多头排列
    if ma5 and price > ma5:
        tech_score += 0.15  # 站上5日线
    else:
        tech_cost -= 0.15  # 跌破MA5扣分
    # MACD完整判定
    ema12 = calc_ema(cache['prices'], 12)
    ema26 = calc_ema(cache['prices'], 26)
    if ema12 is not None and ema26 is not None:
        dif = ema12 - ema26
        if dif > 0:
            # 计算DEA完整序列
            p_tech = cache['prices']
            if len(p_tech) >= 12:
                e12v, e26v = [], []
                for i, pr in enumerate(p_tech):
                    if i == 0: e12v.append(pr); e26v.append(pr)
                    else: e12v.append(pr*2/13+e12v[-1]*(1-2/13)); e26v.append(pr*2/27+e26v[-1]*(1-2/27))
                difv = [a-b for a,b in zip(e12v, e26v)]
                dea_v = []
                for i, d in enumerate(difv):
                    if i == 0: dea_v.append(d)
                    else: dea_v.append(d*2/10+dea_v[-1]*(1-2/10))
                if dif > dea_v[-1]:
                    tech_score += 0.15  # MACD金叉多头区域
                else:
                    macd_dead = True
                    tech_cost -= 0.20  # MACD死叉扣分
    tech_net = tech_score + tech_cost
    if tech_net > 0:
        details.append(f"技术:{tech_net:.2f}")
    elif tech_net < 0:
        details.append(f"技术:{tech_net:.2f}")
    score += tech_net

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

    # 7️⃣ 信号共振因子 v3.0 (-0.3~+0.5): 多空冲突减半
    resonance_bonus = 0.0
    if code in resonance_data:
        r = resonance_data[code]
        if r >= 2:       # buy: 三重共振
            resonance_bonus = 0.5
        elif r >= 1:     # observe: 双重确认
            # 检查 engine_signals 中是否 buy/sell 同时存在 ≥1 → 冲突降权
            conflict = check_resonance_conflict(code)
            if conflict:
                resonance_bonus = 0.25  # 多空冲突 → 减半
            else:
                resonance_bonus = 0.5
        elif r >= 0.5:   # observe_weak: 单一信号看多
            resonance_bonus = 0.15
        elif r <= -1:    # sell: 卖出确认
            resonance_bonus = -0.3
        # r==0: warn/single 中性，不加分
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
        info = prices.get(code, {})
        print(f'  #{i}  {name} ({code})  {arrow} {chg_s}')
        print(f'      价{price}  综合分{total:.2f} = 因子{score_no_morph:.2f}+形态{morph:.2f}')
        # 详细拆解每个因子
        parts = details.split('|')
        for p in parts:
            if not p: continue
            kv = p.split(':', 1)
            if len(kv) != 2: continue
            k, v = kv[0], kv[1]
            vf = float(v) if v.replace('.','').replace('-','').isdigit() else 0
            if k == '涨幅':
                abs_chg = abs(chg_f)
                if '衰减' in v:
                    print(f'      📈 涨幅因子 {v} ← 今日{chg_s}({abs_chg:.1f}%绝对值) × 5日内有过-5%暴跌衰减')
                elif abs_chg >= 8:
                    print(f'      📈 涨幅因子 {v} ← 今日{chg_s}({abs_chg:.1f}%绝对值) 涨超8%衰减至0.1')
                elif abs_chg >= 5:
                    print(f'      📈 涨幅因子 {v} ← 今日{chg_s}({abs_chg:.1f}%绝对值) 5-8%区间衰减至0.3')
                elif abs_chg >= 3:
                    print(f'      📈 涨幅因子 {v} ← 今日{chg_s}({abs_chg:.1f}%绝对值) 3-5%区间衰减至0.7')
                elif abs_chg >= 1:
                    print(f'      📈 涨幅因子 {v} ← 今日{chg_s}({abs_chg:.1f}%绝对值) 1-3%最优区间满分')
                else:
                    print(f'      📈 涨幅因子 {v} ← 今日{chg_s}({abs_chg:.1f}%绝对值) 不足1%衰减至0.2')
            elif k == '量能':
                cache = read_cache(code)
                if cache:
                    avg10v = avgvol_n(cache, 10)
                    ratio = info['vol'] / avg10v if avg10v and avg10v > 0 else 0
                    if '缩量回踩' in v:
                        print(f'      📊 量能因子 {v} ← 量比{ratio:.2f}(<0.8)但均线多头+价>MA20→缩量回踩加分')
                    else:
                        print(f'      📊 量能因子 {v} ← 量比{ratio:.2f}(今日量/{avg10v:.0f}均量)')
            elif k == '位置':
                cache = read_cache(code)
                if cache:
                    ma20 = ma_n(cache, 20)
                    high20 = high_n(cache, 20)
                    if ma20:
                        dist = (price - ma20) / ma20 * 100
                        dist_label = '上方' if dist >= 0 else '下方'
                        extra = ''
                        if high20 and price >= high20:
                            extra = '+0.3(创20日新高)'
                        if dist < 0:
                            extra += '×0.5(价在MA20下方)'
                        print(f'      📍 位置因子 {v} ← 距MA20({ma20:.2f}) {abs(dist):.1f}%{dist_label}{extra}')
            elif k == '趋势':
                cache = read_cache(code)
                if cache:
                    ma20 = ma_n(cache, 20)
                    if ma20 and len(cache['prices']) >= 25:
                        prev_ma20 = sum(cache['prices'][-25:-5]) / len(cache['prices'][-25:-5])
                        slope = (ma20 - prev_ma20) / prev_ma20 * 100
                        slope_label = '上升' if slope > 0.3 else ('走平' if slope > -0.3 else '下降')
                        print(f'      📉 趋势因子 {v} ← MA20斜率{slope:+.2f}%({slope_label})')
            elif k == '技术':
                cache = read_cache(code)
                tech_items = []
                if cache:
                    ma5 = ma_n(cache, 5)
                    ma10 = ma_n(cache, 10)
                    ma20 = ma_n(cache, 20)
                    if ma5 and ma10 and ma20 and ma5 > ma10 > ma20:
                        tech_items.append('多头排列+0.3')
                    if ma5 and price > ma5:
                        tech_items.append('站上MA5+0.15')
                    elif ma5:
                        tech_items.append('跌破MA5-0.15')
                    ema12 = calc_ema(cache['prices'], 12)
                    ema26 = calc_ema(cache['prices'], 26)
                    if ema12 and ema26:
                        dif = ema12 - ema26
                        if dif > 0:
                            p_tech = cache['prices']
                            e12v, e26v = [], []
                            for j, pr in enumerate(p_tech):
                                if j == 0: e12v.append(pr); e26v.append(pr)
                                else: e12v.append(pr*2/13+e12v[-1]*(1-2/13)); e26v.append(pr*2/27+e26v[-1]*(1-2/27))
                            difv = [a-b for a,b in zip(e12v, e26v)]
                            dea_v = []
                            for j, d in enumerate(difv):
                                if j == 0: dea_v.append(d)
                                else: dea_v.append(d*2/10+dea_v[-1]*(1-2/10))
                            if dif > dea_v[-1]:
                                tech_items.append('MACD金叉+0.15')
                            else:
                                tech_items.append('MACD死叉-0.20')
                if tech_items:
                    print(f'      🔧 技术因子 {v} ← {" | ".join(tech_items)}')
                else:
                    print(f'      🔧 技术因子 {v}')
            elif k == '板块':
                sector = SECTOR_MAP.get(code, '未归类')
                print(f'      🏭 板块因子 {v} ← 所属{sector}板块强度加分')
            elif k == '共振':
                r_val = resonance_data.get(code, 0)
                r_label = {2: '三重共振', 1: '双重确认', 0.5: '单一信号看多', 0: '中性', -1: '卖出确认'}.get(r_val, str(r_val))
                conflict = check_resonance_conflict(code)
                if conflict:
                    print(f'      📡 共振因子 {v} ← {r_label} 但多空冲突→减半')
                else:
                    print(f'      📡 共振因子 {v} ← {r_label}')
        # 形态拆解
        if morph > 0:
            cache = read_cache(code)
            if cache:
                p = cache['prices']
                v = cache['vols']
                avg5v = sum(v[-5:]) / 5 if len(v) >= 5 else 0
                morph_items = []
                if len(p) >= 5 and len(v) >= 5:
                    if p[-1] > p[-2] > p[-3] > p[-4]:
                        if v[-1] > avg5v and v[-2] > avg5v:
                            morph_items.append('红三兵(放量)+0.5')
                        else:
                            morph_items.append('红三兵(缩量降级)+0.2')
                    if len(p) >= 3:
                        d1 = (p[-1] - p[-2]) / p[-2] * 100
                        d2 = (p[-2] - p[-3]) / p[-3] * 100
                        if d1 > 0 and d1 > d2:
                            if v[-1] > avg5v:
                                morph_items.append('涨幅加速(放量)+0.3')
                            else:
                                morph_items.append('涨幅加速(缩量)+0.1')
                    if len(v) >= 3 and v[-1] < v[-2] < v[-3]:
                        morph_items.append('缩量回踩+0.2')
                if morph_items:
                    print(f'      🎯 形态因子 {morph:.2f} ← {" | ".join(morph_items)}')
                else:
                    print(f'      🎯 形态因子 {morph:.2f}')
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
