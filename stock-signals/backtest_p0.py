#!/usr/bin/env python3
"""
P0信号评分卡历史回测 v3 — 含形态因子（腾讯API缓存）
新增第5因子：放量大阳线突破后回踩买点识别
"""
import json, sys, os
from datetime import datetime, timedelta

CACHE_DIR = '/root/.openclaw/workspace/stock-signals/cache'

def load_cache(code):
    path = os.path.join(CACHE_DIR, f'{code}.day')
    if not os.path.exists(path):
        return []
    rows = []
    with open(path) as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) >= 2:
                try:
                    close = float(parts[0])
                    volume = float(parts[1])
                    date = parts[2] if len(parts) >= 3 else ''
                    rows.append({'close': close, 'volume': volume, 'date': date})
                except:
                    pass
    return rows

def find_row_at_date(rows, trade_date_str):
    """在缓存中找到最接近trade_date的行（取该日期之前的最新数据）"""
    trade_date = trade_date_str[:10]
    matched_idx = None
    for i, r in enumerate(rows):
        if r['date'] == trade_date:
            matched_idx = i
        elif r['date'] and r['date'] < trade_date:
            matched_idx = i
    if matched_idx is not None and matched_idx >= 1:
        return rows[:matched_idx], rows[matched_idx - 1]
    last_idx = max(0, len(rows) - 2)
    return rows[:last_idx], rows[last_idx - 1] if last_idx > 0 else rows[0]

def calc_ema(prices, n):
    k = 2 / (n + 1)
    ema = prices[0]
    for p in prices[1:]:
        ema = p * k + ema * (1 - k)
    return ema

def calc_dif(prices):
    if len(prices) < 26:
        return None
    ema12 = calc_ema(prices, 12)
    ema26 = calc_ema(prices, 26)
    return ema12 - ema26

def compute_morph_factor(chg_pct, rows, idx, avg10v):
    """
    第5因子：综合形态扫描（对应shell版scan_morphology_signals）
    覆盖所有规则文件中的形态模式，综合评分 -1.0 ~ +1.0
    """
    if idx < 5 or len(rows) < 5:
        return 0.0

    usable = rows[:idx]
    prices = [r['close'] for r in usable]
    volumes = [r['volume'] for r in usable]
    highs = [r['high'] for r in usable]
    lows = [r['low'] for r in usable]
    opens = [r['open'] for r in usable]

    # 计算均线
    def ma(n):
        return sum(prices[-n:]) / n if len(prices) >= n else None
    def avg_vol(n):
        return sum(volumes[-n:]) / n if len(volumes) >= n else None
    def high_n(n):
        return max(highs[-n:]) if len(highs) >= n else None
    def low_n(n):
        return min(lows[-n:]) if len(lows) >= n else None

    ma5 = ma(5); ma10 = ma(10); ma20 = ma(20); ma60 = ma(60)
    avg5v = avg_vol(5); avg10v = avg_vol(10)
    high20 = high_n(20); low20 = low_n(20)
    cur_price = prices[-1] if prices else 0
    cur_vol = volumes[-1] if volumes else 0
    cur_open = opens[-1] if opens else 0

    bull_score = 0.0
    bear_score = 0.0
    bull_count = 0
    bear_count = 0
    rules_fired = []

    def add_bull(score, name):
        nonlocal bull_score, bull_count
        bull_score += score
        bull_count += 1
        rules_fired.append(name)

    def add_bear(score, name):
        nonlocal bear_score, bear_count
        bear_score += score
        bear_count += 1
        rules_fired.append(name)

    # ── 1. K线形态 ──
    if len(usable) >= 4:
        c4 = usable[-4]['close']  # 3日前
        c3 = usable[-3]['close']  # 2日前
        c2 = usable[-2]['close']  # 昨日
        c1 = usable[-1]['close']  # 今日

        # 红三兵：连续3日上涨>0.3%
        red_three = True
        for i in range(3):
            pi = usable[-4+i]['close']
            ci = usable[-3+i]['close']
            if pi <= 0 or (ci - pi)/pi*100 < 0.3:
                red_three = False
                break
        if red_three:
            add_bull(0.30, 'red_three')

        # 三只乌鸦：连续3日下跌>0.5%
        three_crows = True
        for i in range(3):
            pi = usable[-4+i]['close']
            ci = usable[-3+i]['close']
            if pi <= 0 or (ci - pi)/pi*100 > -0.5:
                three_crows = False
                break
        if three_crows:
            add_bear(0.30, 'three_crows')

        # 锤子线：长下影线（实体小，下影>实体1.5倍，今天收盘>开盘）
        if cur_open and cur_open > 0:
            body = abs(c1 - cur_open)
            shadow_down = c1 - (min(lows[-3:]) if lows else 0)
            # 简化：用当日低点与收盘/开盘的差值
            lowest_today = lows[-1]
            shadow_len = c1 - lowest_today if c1 > cur_open else cur_open - lowest_today
            if 0 < body < (c1 * 0.02) and shadow_len > body * 1.5:
                if c1 < (ma20 or 999):
                    add_bull(0.20, 'hammer')
                elif c1 > (ma20 or 0) * 1.1:
                    add_bear(0.25, 'hanging_man')

        # 跳空缺口
        prev_close = usable[-2]['close'] if len(usable) >= 2 else 0
        if cur_open and prev_close > 0:
            gap = (cur_open - prev_close) / prev_close * 100
            if gap > 1:
                add_bull(0.20, 'gap_up')
            elif gap < -1:
                add_bear(0.25, 'gap_down')

    # ── 2. 均线趋势形态 ──
    if ma5 and ma10 and ma20:
        if ma60 and ma5 > ma10 > ma20 > ma60:
            add_bull(0.50, 'bullish_arrangement')
        elif ma5 < ma10 < ma20:
            add_bear(0.50, 'bearish_arrangement')

        # 金叉/死叉
        diff_5_20 = abs(ma5 - ma20)
        if ma5 > ma20 and diff_5_20 < ma20 * 0.03:
            add_bull(0.30, 'ma_golden_cross')
        elif ma5 < ma20 and diff_5_20 < ma20 * 0.03:
            add_bear(0.30, 'ma_death_cross')

    # ── 3. MACD信号 ──
    if len(prices) >= 26:
        dif = calc_dif(prices)
        prev_prices = prices[:-1]
        prev_dif = calc_dif(prev_prices) if len(prev_prices) >= 26 else None
        if dif is not None:
            if dif > 0:
                add_bull(0.15, 'macd_above_zero')
            else:
                add_bear(0.15, 'macd_below_zero')
            # 底背离/顶背离：价格极值与DIF趋势背离
            if len(prices) >= 30 and prev_dif is not None:
                price_low5 = min(prices[-5:])
                price_low10 = min(prices[-10:-5]) if len(prices) >= 10 else price_low5
                price_high5 = max(prices[-5:])
                price_high10 = max(prices[-10:-5]) if len(prices) >= 10 else price_high5
                # 计算多组DIF值
                dif_vals = []
                for i in range(10, 0, -1):
                    seg = prices[:-i] if len(prices) > i else prices
                    if len(seg) >= 26:
                        dif_vals.append(calc_dif(seg))
                    else:
                        dif_vals.append(dif)
                if len(dif_vals) >= 5:
                    if price_low5 <= price_low10 * 0.99 and dif_vals[-1] > dif_vals[-5]:
                        add_bull(0.40, 'macd_bottom_div')
                    if price_high5 >= price_high10 * 1.01 and dif_vals[-1] < dif_vals[-5]:
                        add_bear(0.40, 'macd_top_div')

    # ── 4. 突破形态 ──
    if high20 and cur_price > high20:
        add_bull(0.35, 'breakout_up')
    if low20 and cur_price < low20:
        add_bear(0.30, 'breakdown')
    # 2B：前日突破、今日回到区间内
    if len(usable) >= 3:
        prev_c = usable[-2]['close']
        if prev_c and high20 and low20:
            if prev_c > high20 and cur_price < high20 and (prev_c - high20)/high20 < 0.03:
                add_bear(0.50, '2b_fake_breakout')
            if prev_c < low20 and cur_price > low20 and (low20 - prev_c)/low20 < 0.03:
                add_bull(0.50, '2b_fake_breakdown')

    # ── 5. 量价形态 ──
    if avg10v and avg10v > 0 and cur_vol > 0:
        vol_ratio = cur_vol / avg10v
        if vol_ratio < 0.5 and chg_pct < 0:
            add_bull(0.20, 'vol_down_shrink')  # 价跌量缩-洗盘
        elif vol_ratio < 0.7:
            add_bull(0.10, 'volume_shrink')
        elif vol_ratio > 2:
            add_bear(0.10, 'volume_surge')  # 巨量

        # 价量配合
        abs_chg = abs(chg_pct)
        if abs_chg > 2:
            if chg_pct > 0 and vol_ratio > 1.3:
                add_bull(0.25, 'vol_up_with_price')
            elif chg_pct > 0 and vol_ratio < 0.8:
                add_bear(0.15, 'vol_up_no_vol')
            elif chg_pct < 0 and vol_ratio > 1.3:
                add_bear(0.30, 'vol_down_with_vol')
            elif chg_pct < 0 and vol_ratio < 0.7:
                add_bull(0.20, 'vol_down_shrink')

    # ── 6. 筹码密集区 ──
    if ma20 and cur_price > 0:
        dev = abs((cur_price - ma20) / ma20 * 100)
        if dev < 1.5:
            add_bull(0.10, 'density_zone')  # 20日线附近-筹码密集

    # ── 综合评分 ──
    combined = bull_score - abs(bear_score)

    # 多形态共振加分
    multi_bonus = 0.0
    if bull_count >= 2:
        multi_bonus = 0.15
    if bull_count >= 3:
        multi_bonus = 0.30

    # 经典组合加分
    rf = ','.join(rules_fired)
    if 'red_three' in rf and 'breakout_up' in rf:
        multi_bonus += 0.20
    if 'bullish_arrangement' in rf and 'ma_golden_cross' in rf:
        multi_bonus += 0.15
    if 'macd_bottom_div' in rf and 'vol_down_shrink' in rf:
        multi_bonus += 0.25
    if '2b_fake_breakdown' in rf and 'hammer' in rf:
        multi_bonus += 0.20
    if 'gap_up' in rf and 'vol_up_with_price' in rf:
        multi_bonus += 0.15

    total = combined + multi_bonus
    total = max(-1.0, min(1.0, total))

    return total

def compute_factors_snapshot(buy_price, buy_chg_pct, cache_rows, cache_up_to_idx):
    """
    基于某个时间点的缓存快照计算评分（含形态因子）
    
    返回：{total: float, morph: float, total_ext: float, details: dict, pass: bool}
    """
    if cache_up_to_idx <= 0:
        return {'total': 0, 'morph': 0, 'total_ext': 0, 'details': {'涨幅': 0, '量能': 0, '位置': 0, '趋势': 0}, 'pass': False}
    
    usable = cache_rows[:cache_up_to_idx]
    if len(usable) < 5:
        return {'total': 0, 'morph': 0, 'total_ext': 0, 'details': {'涨幅': 0, '量能': 0, '位置': 0, '趋势': 0}, 'pass': False}
    
    score = 0.0
    details = {}
    prices = [r['close'] for r in usable]
    volumes = [r['volume'] for r in usable]
    
    # 1️⃣ 涨幅因子（线性: max(0, 1-|chg|/5)）
    abs_chg = abs(buy_chg_pct)
    chg_score = max(0, 1 - abs_chg / 5)
    score += chg_score; details['涨幅'] = round(chg_score, 2)
    
    # 2️⃣ 量能因子
    latest_vol = volumes[-1] if volumes else 0
    avg10v = sum(volumes[-10:]) / 10 if len(volumes) >= 10 else (sum(volumes) / len(volumes) if volumes else 0)
    
    if avg10v > 0 and latest_vol:
        ratio = latest_vol / avg10v
        if ratio >= 1.5: score += 1.0; details['量能'] = 1.0
        elif ratio >= 1.3: score += 0.5; details['量能'] = 0.5
        else: details['量能'] = 0.0
    else:
        details['量能'] = 0
    
    price = buy_price
    
    # 3️⃣ 位置因子
    ma20 = sum(prices[-20:]) / 20 if len(prices) >= 20 else sum(prices) / len(prices)
    high20 = max(prices[-20:]) if len(prices) >= 20 else max(prices)
    
    pos_score = 0.0
    if ma20 > 0 and price:
        dist_to_ma20 = abs((price - ma20) / ma20 * 100)
        if dist_to_ma20 <= 5: pos_score = 0.5
        if high20 and price >= high20: pos_score += 0.5
        if dist_to_ma20 > 10: pos_score *= 0.5
    score += min(pos_score, 1.0)
    details['位置'] = round(pos_score, 2)
    
    # 4️⃣ 趋势因子
    trend_score = 0.0
    dist_pct = (price - ma20) / ma20 * 100 if ma20 > 0 else 0
    
    if len(prices) >= 25 and ma20 > 0:
        prev_prices = prices[-25:-5]
        if len(prev_prices) >= 20:
            prev_ma20 = sum(prev_prices[-20:]) / 20
            slope = (ma20 - prev_ma20) / prev_ma20 * 100
            if slope > 0.3: trend_score = 1.0
            elif slope < -0.3: trend_score = 0.0
            else: trend_score = 0.5
    elif price > ma20:
        trend_score = 0.5
    
    if dist_pct > 10:
        trend_score *= 0.5
    
    score += trend_score
    details['趋势'] = trend_score
    
    # 5️⃣ 形态因子（0-0.5，额外加分）
    morph_score = compute_morph_factor(buy_chg_pct, cache_rows, cache_up_to_idx, avg10v)
    total_ext = score + morph_score
    details['形态'] = morph_score
    
    return {
        'total': round(score, 2),
        'morph': morph_score,
        'total_ext': round(total_ext, 2),
        'details': details,
        'pass': score >= 2.5,       # 4因子评分决定阻挡/通过
        'morph_bonus': morph_score  # 形态因子额外加分，不计入pass判定
    }


# ===== 历史交易数据 =====
trades = [
    {"stock": "利欧股份", "code": "002131", "date": "2026-05-15 09:35", "price": 6.56, "buy_chg": 0.77, "pnl": 674, "pnl_pct": "+3.42%"},
    {"stock": "深桑达Ａ", "code": "000032", "date": "2026-05-18 09:36", "price": 18.76, "buy_chg": 2.01, "pnl": 350, "pnl_pct": "+3.73%"},
    {"stock": "五洲新春", "code": "603667", "date": "2026-05-15 09:35", "price": 77.11, "buy_chg": 0.21, "pnl": 364, "pnl_pct": "+4.72%"},
    {"stock": "雷科防务", "code": "002413", "date": "2026-05-15 09:35", "price": 12.75, "buy_chg": 0.16, "pnl": -84, "pnl_pct": "-0.94%"},
    {"stock": "神州数码①", "code": "000034", "date": "2026-05-18 10:30", "price": 41.64, "buy_chg": 2.26, "pnl": -1204, "pnl_pct": "-28.92%"},  # 大亏
    {"stock": "创元科技", "code": "000551", "date": "2026-05-18 09:36", "price": 17.19, "buy_chg": 0.59, "pnl": 0, "pnl_pct": "持仓中"},
    {"stock": "神州数码②", "code": "000034", "date": "2026-05-19 10:30", "price": 29.83, "buy_chg": 0.64, "pnl": 292, "pnl_pct": "+3.26%"},
    {"stock": "科大讯飞①", "code": "002230", "date": "2026-05-19 10:30", "price": 48.95, "buy_chg": 0.49, "pnl": 7, "pnl_pct": "+0.14%"},
    {"stock": "华天科技", "code": "002185", "date": "2026-05-20 10:30", "price": 15.98, "buy_chg": 4.44, "pnl": -350, "pnl_pct": "-3.65%"},  # 追高
    {"stock": "航天机电", "code": "600151", "date": "2026-05-20 10:30", "price": 16.12, "buy_chg": 2.54, "pnl": -351, "pnl_pct": "-3.62%"},  # 追高
    {"stock": "航天发展①", "code": "000547", "date": "2026-05-21 10:30", "price": 24.63, "buy_chg": 1.78, "pnl": -378, "pnl_pct": "-5.12%"},
    {"stock": "科大讯飞②", "code": "002230", "date": "2026-05-21 10:30", "price": 48.92, "buy_chg": 1.92, "pnl": -182, "pnl_pct": "-3.72%"},
    {"stock": "航宇微", "code": "300053", "date": "2026-05-15 09:35", "price": 19.55, "buy_chg": 0.15, "pnl": -285, "pnl_pct": "-2.91%"},
    {"stock": "紫光国微", "code": "002049", "date": "2026-05-22 09:36", "price": 78.04, "buy_chg": 0.19, "pnl": 0, "pnl_pct": "持仓中"},
    {"stock": "三维通信", "code": "002115", "date": "2026-05-22 09:36", "price": 12.99, "buy_chg": 0.15, "pnl": 0, "pnl_pct": "持仓中"},
    {"stock": "拓维信息", "code": "002261", "date": "2026-05-22 09:36", "price": 32.32, "buy_chg": 0.15, "pnl": 0, "pnl_pct": "持仓中"},
    {"stock": "航天发展②", "code": "000547", "date": "2026-05-22 13:36", "price": 23.46, "buy_chg": 0.21, "pnl": 0, "pnl_pct": "持仓中"},
]

print("=" * 130)
print("📊 P0 信号评分卡 · 历史回测 v3（含形态因子 · 腾讯API缓存）")
print("=" * 130)
print(f"{'标的':<12} {'买入价':>8} {'涨跌幅':>8} {'量比':>6} {'评分':>5} {'形态':>5} {'总分':>5} {'判定':>8} {'实际结果':>18} {'评分详情'}")
print("-" * 130)

passed, blocked = 0, 0
blocked_loss_total = 0
passed_profit_total = 0
morph_count = 0

for t in trades:
    rows = load_cache(t['code'])
    if not rows:
        print(f"{t['stock']:<12} - 无缓存，跳过")
        continue
    
    # Find cache state at trade date
    trade_day = t['date'][:10]
    usable_end = None
    for i, r in enumerate(rows):
        if r['date'] and r['date'] >= trade_day:
            usable_end = i
            break
    if usable_end is None:
        usable_end = len(rows) - 1
    
    buy_chg = t['buy_chg']
    latest_row = rows[usable_end] if usable_end < len(rows) else rows[-1]
    cache_before = rows[:usable_end] if usable_end else rows
    
    vol = latest_row['volume'] if latest_row else 0
    price = t['price']
    
    volumes_before = [r['volume'] for r in cache_before[-15:]] if len(cache_before) >= 15 else [r['volume'] for r in cache_before]
    avg10v = sum(volumes_before[-10:]) / 10 if len(volumes_before) >= 10 else (sum(volumes_before) / len(volumes_before) if volumes_before else 0)
    vol_ratio = vol / avg10v if avg10v > 0 else 0
    
    prices_before = [r['close'] for r in cache_before]
    ma20 = sum(prices_before[-20:]) / 20 if len(prices_before) >= 20 else sum(prices_before) / len(prices_before)
    high20 = max(prices_before[-20:]) if len(prices_before) >= 20 else max(prices_before)
    
    # Score with morphology factor
    result = compute_factors_snapshot(price, buy_chg, rows, usable_end)
    score4 = result['total']       # 4因子评分
    morph = result['morph']        # 形态因子
    total_ext = result['total_ext'] # 总分（含形态）
    d = result['details']
    
    verdict = '✅ 通过' if result['pass'] else '❌ 阻挡'
    
    if morph > 0:
        morph_count += 1
    
    if result['pass']:
        passed += 1
        if t['pnl'] and t['pnl'] > 0:
            passed_profit_total += t['pnl']
    else:
        blocked += 1
        if t['pnl'] and t['pnl'] < 0:
            blocked_loss_total += t['pnl']
    
    pnl_str = f"¥{t['pnl']:+.0f}" if t['pnl'] != 0 else t['pnl_pct']
    
    morph_str = f"+{morph:.1f}" if morph > 0 else f"{morph:.1f}"
    detail_str = f"涨:{d.get('涨幅',0):.1f} 量:{d.get('量能',0):.1f} 位:{d.get('位置',0):.1f} 势:{d.get('趋势',0):.1f} 形:{d.get('形态',0):.1f}"
    
    print(f"{t['stock']:<12} {price:>8.2f} {buy_chg:>+7.2f}% {vol_ratio:>5.1f}x {score4:>4.1f} {morph_str:>5} {total_ext:>4.1f} {verdict:>10} {pnl_str:>18} {detail_str}")

print("-" * 130)

# 统计
print(f"\n📈 综合统计:")
print(f"  总交易数: {passed + blocked}")
print(f"  评分卡通过（4因子≥2.5）: {passed} 笔")
print(f"  评分卡阻挡（4因子<2.5）: {blocked} 笔")
print(f"  形态因子激活: {morph_count} 笔（放量大阳突破回踩买点识别）")

print(f"\n🔴 已阻挡的大额亏损交易（4因子评分<2.5）：")
for t in trades:
    if t['pnl'] is not None and t['pnl'] < 0:
        rows = load_cache(t['code'])
        if not rows: continue
        for i, r in enumerate(rows):
            if r['date'] and r['date'] >= t['date'][:10]:
                result = compute_factors_snapshot(t['price'], t['buy_chg'], rows, i)
                if not result['pass']:
                    morph_tag = f" +形{result['morph']:.1f}" if result['morph'] > 0 else ""
                    print(f"  ❌ {t['stock']:<12} 买入@{t['price']:.2f} 亏损¥{t['pnl']:+.0f} ({t['pnl_pct']}) | 4因子评分{result['total']:.1f}/4{morph_tag} | 含形态总分{result['total_ext']:.1f}")
                break

print(f"\n💰 评分卡阻挡总亏损: ¥{blocked_loss_total:+.0f}（本月最大亏损源被阻断）")

print("\n" + "=" * 130)
print("✅ P0评分卡v3历史回测完成（腾讯API缓存·含形态因子）")
print("=" * 130)
