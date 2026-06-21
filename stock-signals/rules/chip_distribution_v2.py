#!/usr/bin/env python3
"""
筹码分布 V3 — Close锚定三角分配 + 动态集中度 + 横盘门控
===========================================================
修正对照（按优先级）：
  V2: 三角中心=(H+L)/2  →  V3: 三角中心=Close（收盘价锚定）
  V2: 集中度=±3%固定    →  V3: 集中度=max(ATR×1.5, range×10%)
  V2: 60日固定窗口      →  V3: 横盘检测器门控 + 换手加权窗口
  V2: 位置百分位孤立    →  V3: 换手充分度同伴变量

输入：日线缓存文件（格式: close open high low vol date）
输出：JSON信号行（兼容 engine.sh 现有信号体系）

用法：
  python3 chip_distribution_v2.py <cache_path> <current_price>
"""

import json
import sys
import math

# ============================================================
# 配置参数
# ============================================================
LOOKBACK_DAYS = 60          # 最大计算窗口
MIN_BINS = 30               # 最少价位分段数
CONSOLIDATE_THRESHOLD = 0.15  # 横盘判定：60日振幅<15%
CHURN_MIN = 0.3             # 换手充分度阈值（低于此则筹码结论不可靠）
PEAK_CONCENTRATION = 0.55   # 单峰判定：集中度阈值
SECOND_PEAK_RATIO = 0.50    # 单峰判定：次峰/主峰 < 此值
LOW_POSITION_THRESHOLD = 45  # 低位判定
HIGH_POSITION_THRESHOLD = 80 # 高位判定
PROFIT_RATIO_HIGH = 80      # 获利盘过高警示
PROFIT_RATIO_LOW = 20       # 获利盘过低警示
CONCENTRATION_WIDTH_RANGE = 0.10  # 集中度带宽：60日区间的10%
CONCENTRATION_WIDTH_ATR = 1.5     # 集中度带宽：ATR的1.5倍


def load_kline(cache_path):
    """加载日线缓存"""
    rows = []
    with open(cache_path) as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) >= 6:
                try:
                    rows.append({
                        'close': float(parts[0]),
                        'open': float(parts[1]),
                        'high': float(parts[2]),
                        'low': float(parts[3]),
                        'vol': float(parts[4]),
                        'date': parts[5]
                    })
                except ValueError:
                    pass
            elif len(parts) >= 3:
                try:
                    rows.append({
                        'close': float(parts[0]),
                        'vol': float(parts[1]),
                        'open': float(parts[0]),
                        'high': float(parts[0]),
                        'low': float(parts[0]),
                        'date': parts[2]
                    })
                except ValueError:
                    pass
    return rows


def compute_atr(rows, period=14):
    """计算ATR（平均真实波幅）"""
    if len(rows) < period + 1:
        return 0
    trs = []
    for i in range(-period, 0):
        r = rows[i]
        prev_close = rows[i - 1]['close']
        tr = max(r['high'] - r['low'],
                 abs(r['high'] - prev_close),
                 abs(r['low'] - prev_close))
        trs.append(tr)
    return sum(trs) / len(trs)


def is_consolidating(rows, threshold=CONSOLIDATE_THRESHOLD):
    """
    横盘检测器：60日内振幅 < threshold
    返回 (is_consolidating, amplitude_pct)
    """
    closes = [r['close'] for r in rows]
    low = min(closes)
    high = max(closes)
    amp = (high - low) / low if low > 0 else 0
    return amp < threshold, amp * 100


def compute_churn_ratio(rows, lookback=60):
    """
    换手充分度：窗口内总成交量 / 流通股本估算
    没有流通股本数据时，用窗口内日均量 / 前120日均量 作为相对换手率
    """
    window = rows[-lookback:] if len(rows) >= lookback else rows
    total_vol = sum(r['vol'] for r in window)
    avg_vol = total_vol / len(window)

    # 用更长期的均值做基准
    if len(rows) > lookback + 60:
        baseline = rows[-(lookback + 60):-lookback]
        baseline_avg = sum(r['vol'] for r in baseline) / len(baseline)
    else:
        baseline_avg = avg_vol

    return avg_vol / baseline_avg if baseline_avg > 0 else 1.0


def select_window(rows):
    """
    窗口选择器：
    - 横盘期 → 全60日
    - 趋势期 → 只取最近N天（成交量加权截断）
    """
    consolidating, amp = is_consolidating(rows)
    window = rows[-LOOKBACK_DAYS:] if len(rows) >= LOOKBACK_DAYS else rows

    if not consolidating and len(window) >= 30:
        # 趋势行情：用"成交量加权活跃窗口"
        # 取最近30日中，成交量 > 60日均量×0.8 的天
        avg_vol_60 = sum(r['vol'] for r in window) / len(window)
        active = [r for r in window[-30:] if r['vol'] > avg_vol_60 * 0.8]
        if len(active) >= 15:
            return active, consolidating, amp

    return window, consolidating, amp


def build_chip_distribution(rows, current_price):
    """
    V3 筹码分布：Close锚定三角分配 + 动态集中度
    """
    window, consolidating, amp = select_window(rows)
    if len(window) < 15:
        return None

    # 价格区间
    all_highs = [r['high'] for r in window]
    all_lows = [r['low'] for r in window]
    price_min = min(all_lows)
    price_max = max(all_highs)
    price_range = price_max - price_min

    if price_range <= 0:
        return None

    # 自适应分段
    num_bins = max(MIN_BINS, int(price_range / (price_min * 0.02)) + 1)
    bin_width = price_range / num_bins
    bins_center = [price_min + (i + 0.5) * bin_width for i in range(num_bins)]
    chip_amount = [0.0] * num_bins

    # 计算ATR用于分配宽度
    atr = compute_atr(rows)

    # 逐日分配筹码（Close锚定三角分配）
    for r in window:
        vol = r['vol']
        if vol <= 0:
            continue

        close_px = r['close']
        high_px = r['high']
        low_px = r['low']
        day_range = high_px - low_px

        if day_range <= 0:
            # 一字板：全部筹码集中在收盘价
            for i, center in enumerate(bins_center):
                if abs(center - close_px) < bin_width:
                    chip_amount[i] += vol
                    break
            continue

        # 分配宽度 = max(日振幅, ATR×k)，防窄幅日尖峰
        spread = max(day_range, atr * 0.8)
        half_spread = spread / 2.0

        for i, bin_center in enumerate(bins_center):
            dist = abs(bin_center - close_px)
            if dist >= half_spread:
                continue
            # 三角权重：中心在Close，向两端线性递减
            weight = 1.0 - dist / half_spread
            chip_amount[i] += vol * weight

    # 归一化
    total_chips = sum(chip_amount)
    if total_chips > 0:
        chip_amount = [c / total_chips * 100 for c in chip_amount]
    else:
        return None

    # 找主峰和次峰（滑动窗口法）
    peak_idx = 0
    peak_val = 0
    second_peak_idx = 0
    second_peak_val = 0

    # 窄幅横盘时用更大的窗口抑制噪声峰
    if consolidating:
        window_size = max(5, num_bins // 6)
    else:
        window_size = max(3, num_bins // 10)

    for i in range(window_size, num_bins - window_size):
        val = chip_amount[i]
        is_peak = True
        for j in range(i - window_size, i + window_size + 1):
            if j != i and chip_amount[j] >= val:
                is_peak = False
                break
        if is_peak and val > peak_val:
            second_peak_val = peak_val
            second_peak_idx = peak_idx
            peak_val = val
            peak_idx = i
        elif is_peak and val > second_peak_val:
            second_peak_val = val
            second_peak_idx = i

    peak_price = bins_center[peak_idx] if peak_val > 0 else current_price
    second_peak_price = bins_center[second_peak_idx] if second_peak_val > 0 else 0

    # 动态集中度带宽：max(ATR×1.5, range×10%)
    width_atr = atr * CONCENTRATION_WIDTH_ATR
    width_range = price_range * CONCENTRATION_WIDTH_RANGE
    effective_width = max(width_atr, width_range)

    concentrated = 0.0
    for i, center in enumerate(bins_center):
        if abs(center - peak_price) <= effective_width:
            concentrated += chip_amount[i]
    concentration = concentrated / sum(chip_amount) * 100 if sum(chip_amount) > 0 else 0

    # 获利盘比例
    profit_chips = 0.0
    for i, center in enumerate(bins_center):
        if center <= current_price:
            profit_chips += chip_amount[i]
    profit_ratio = profit_chips / sum(chip_amount) * 100 if sum(chip_amount) > 0 else 0

    # 30日VWAP
    recent_30 = rows[-30:] if len(rows) >= 30 else rows
    total_vol_vwap = sum(r['vol'] for r in recent_30 if r['vol'] > 0)
    vwap_30 = sum(r['close'] * r['vol'] for r in recent_30 if r['vol'] > 0) / total_vol_vwap if total_vol_vwap > 0 else current_price

    # 位置百分位
    position_pct = ((current_price - price_min) / price_range * 100) if price_range > 0 else 50
    peak_position_pct = ((peak_price - price_min) / price_range * 100) if price_range > 0 else 50

    # 换手充分度
    churn = compute_churn_ratio(rows)

    return {
        'bins': list(zip(bins_center, chip_amount)),
        'peak_price': round(peak_price, 2),
        'peak_amount': round(peak_val, 2),
        'second_peak_price': round(second_peak_price, 2),
        'second_peak_amount': round(second_peak_val, 2),
        'concentration': round(concentration, 1),
        'concentration_width': round(effective_width, 2),
        'profit_ratio': round(profit_ratio, 1),
        'vwap_30': round(vwap_30, 2),
        'position_pct': round(position_pct, 1),
        'peak_position_pct': round(peak_position_pct, 1),
        'price_low': round(price_min, 2),
        'price_high': round(price_max, 2),
        'consolidating': consolidating,
        'amplitude_pct': round(amp, 1),
        'churn_ratio': round(churn, 2),
        'window_days': len(window),
    }


def classify_peak_pattern(dist):
    """
    根据筹码分布判定峰形态
    返回: (pattern_type, pattern_detail, confidence)
      confidence: 'high' | 'medium' | 'low'
    """
    if dist is None:
        return 'unknown', '数据不足', 'low'

    peak_val = dist['peak_amount']
    second_val = dist['second_peak_amount']
    concentration = dist['concentration']
    peak_pos = dist['peak_position_pct']
    consolidating = dist['consolidating']
    churn = dist['churn_ratio']
    amp = dist['amplitude_pct']

    # 换手充分度门控：换手不足时降低置信度
    churn_adequate = churn >= CHURN_MIN

    # 单峰判定
    # 横盘时：如果主峰和次峰距离 < 集中度带宽，视为同一宽峰
    peak_gap = abs(dist['peak_price'] - dist['second_peak_price'])
    peaks_merged = consolidating and peak_gap < dist.get('concentration_width', 0) * 2

    is_single_peak = (concentration >= PEAK_CONCENTRATION * 100 and
                      (second_val == 0 or second_val < peak_val * SECOND_PEAK_RATIO or peaks_merged))

    # 置信度
    if consolidating and churn_adequate:
        confidence = 'high'
    elif consolidating:
        confidence = 'medium'
    elif churn_adequate:
        confidence = 'medium'
    else:
        confidence = 'low'

    if is_single_peak:
        if consolidating:
            # 横盘期的单峰 → 真正的密集区
            # 横盘时位置百分位无意义，直接用横盘+集中度判定
            # 横盘+高集中度 = 筹码充分换手的密集区，视为低位密集（横盘本身就是低位特征）
            return 'single_low', f"低位单峰密集(横盘{amp:.0f}%,峰@{dist['peak_price']},集中度{concentration}%)", confidence
        else:
            # 趋势行情中的单峰 → 可能只是趋势中的筹码集中，不是底部密集
            if peak_pos < LOW_POSITION_THRESHOLD:
                return 'single_low', f"低位单峰(趋势{amp:.0f}%,峰@{dist['peak_price']},集中度{concentration}%)", 'medium'
            elif peak_pos > HIGH_POSITION_THRESHOLD:
                return 'single_upper', f"高位单峰(趋势{amp:.0f}%,峰@{dist['peak_price']},集中度{concentration}%)", 'medium'
            else:
                return 'single_mid', f"中位单峰(趋势{amp:.0f}%,峰@{dist['peak_price']},集中度{concentration}%)", 'medium'
    elif second_val > 0 and second_val > peak_val * 0.3:
        return 'dual', f"双峰分布(主峰@{dist['peak_price']},次峰@{dist['second_peak_price']})", confidence
    elif concentration < 30:
        return 'scattered', f"筹码分散(集中度仅{concentration}%)", confidence
    else:
        return 'transition', f"筹码迁移中(集中度{concentration}%)", confidence


def generate_signals(dist, current_price):
    """根据筹码分布生成信号"""
    signals = []

    if dist is None:
        return signals

    pattern, detail, confidence = classify_peak_pattern(dist)

    # 信号1：低位单峰密集
    if pattern == 'single_low':
        strength = 'very_high' if confidence == 'high' else 'high'
        signals.append({
            "rule": "chip_peak_low_single",
            "direction": "buy_signal",
            "strength": strength,
            "note": f"低位单峰密集-{detail}",
            "peak_price": dist['peak_price'],
            "concentration": dist['concentration'],
            "profit_ratio": dist['profit_ratio'],
            "confidence": confidence
        })

    # 信号2：高位单峰密集
    if pattern == 'single_upper':
        strength = 'high' if confidence == 'high' else 'medium'
        signals.append({
            "rule": "chip_peak_upper_single",
            "direction": "bearish_warn",
            "strength": strength,
            "note": f"高位单峰密集-警惕出货-{detail}",
            "peak_price": dist['peak_price'],
            "concentration": dist['concentration'],
            "profit_ratio": dist['profit_ratio'],
            "confidence": confidence
        })

    # 信号3：双峰分布
    if pattern == 'dual':
        signals.append({
            "rule": "chip_dual_peak",
            "direction": "neutral",
            "strength": "info",
            "note": f"双峰筹码分布-{detail}",
            "peak_price": dist['peak_price'],
            "second_peak_price": dist['second_peak_price'],
            "confidence": confidence
        })

    # 信号4：获利盘比例
    if dist['profit_ratio'] >= PROFIT_RATIO_HIGH:
        signals.append({
            "rule": "chip_profit_high",
            "direction": "bearish_warn",
            "strength": "low",
            "note": f"获利盘{dist['profit_ratio']}%-兑现压力大"
        })
    elif dist['profit_ratio'] <= PROFIT_RATIO_LOW:
        signals.append({
            "rule": "chip_profit_low",
            "direction": "overshoot",
            "strength": "low",
            "note": f"获利盘仅{dist['profit_ratio']}%-超跌区域"
        })

    # === V1 向后兼容信号 ===
    deviation = (current_price / dist['vwap_30'] - 1) * 100

    if abs(deviation) < 5 and dist['position_pct'] < 45:
        signals.append({
            "rule": "chip_density_low",
            "direction": "buy_signal",
            "strength": "medium",
            "note": f"低位筹码密集-距主力成本{abs(deviation):.1f}%,位置{dist['position_pct']:.0f}%"
        })

    if deviation > 15:
        signals.append({
            "rule": "chip_deviation_high",
            "direction": "bearish_warn",
            "strength": "medium",
            "note": f"大幅偏离成本{deviation:.0f}%-获利盘丰厚"
        })

    if deviation < -8:
        signals.append({
            "rule": "chip_below_cost",
            "direction": "overshoot",
            "strength": "medium",
            "note": f"低于主力成本{abs(deviation):.0f}%-超跌区域"
        })

    return signals


def main():
    if len(sys.argv) < 3:
        sys.exit(0)

    cache_path = sys.argv[1]
    current_price = float(sys.argv[2])

    rows = load_kline(cache_path)
    if len(rows) < 15:
        sys.exit(0)

    dist = build_chip_distribution(rows, current_price)
    signals = generate_signals(dist, current_price)

    for sig in signals:
        print(json.dumps(sig, ensure_ascii=False, separators=(",", ":")),
              flush=True)


if __name__ == '__main__':
    main()
