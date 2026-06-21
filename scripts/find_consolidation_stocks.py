#!/usr/bin/env python3
"""
找"香农芯创形态"标的 —— 横盘箱体 + 充分换手 + 筹码低位单峰密集

香农芯创(300475)典型特征（2026-04-29~05-29）：
  - 20天横盘箱体，振幅17.4%
  - 累计换手929%（日均46.5%）
  - 筹码低位单峰密集，集中度93%
  - 获利盘适中（横盘末期~50%）
  - 当前价在箱体中位附近

用法：
  python3 scripts/find_consolidation_stocks.py
  python3 scripts/find_consolidation_stocks.py --max-amp 20 --min-conc 75 --min-profit 20 --max-profit 80
"""

import subprocess, json, os, sys, re

WORKSPACE = "/root/.openclaw/workspace"
CACHE_DIR = f"{WORKSPACE}/stock-signals/cache"
CHIP_SCRIPT = f"{WORKSPACE}/stock-signals/rules/chip_distribution_v2.py"
TOOLS_MD = f"{WORKSPACE}/TOOLS.md"

def load_kline(code):
    for ext in ['.day', '.txt', '']:
        path = f"{CACHE_DIR}/{code}{ext}"
        if os.path.exists(path):
            break
    else:
        return None
    rows = []
    with open(path) as f:
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
    return rows

def find_best_window(rows, min_days=15, max_days=40, max_amp=18.0):
    """找最近60天内最佳横盘窗口"""
    n = len(rows)
    if n < min_days:
        return None
    best = None
    search_range = rows[-60:] if n > 60 else rows
    for wlen in range(min_days, min(max_days + 1, len(search_range))):
        for start in range(len(search_range) - wlen):
            sub = search_range[start:start+wlen]
            closes = [r['close'] for r in sub]
            lo = min(closes)
            hi = max(closes)
            amp = (hi - lo) / lo * 100 if lo > 0 else 999
            if amp <= max_amp:
                total_vol = sum(r['vol'] for r in sub)
                avg = sum(closes) / len(closes)
                # 评分：越近越好、振幅越小越好、窗口越长越好
                recency = (len(search_range) - (start + wlen)) / len(search_range)
                score = (1 - recency) * 0.5 + (1 - amp / max_amp) * 0.3 + (wlen / max_days) * 0.2
                if best is None or score > best['score']:
                    best = {
                        'wlen': wlen, 'amp': amp, 'total_vol': total_vol,
                        'avg': avg, 'lo': lo, 'hi': hi,
                        'start_date': sub[0]['date'], 'end_date': sub[-1]['date'],
                        'score': score,
                    }
                break  # 每个长度只取最早一个
    return best

def run_chip(code, cache_path, price):
    """运行筹码分布脚本，返回信号列表"""
    try:
        r = subprocess.run(['python3', CHIP_SCRIPT, cache_path, str(price)],
                          capture_output=True, text=True, timeout=10)
        signals = []
        for line in r.stdout.strip().split('\n'):
            if line:
                try:
                    signals.append(json.loads(line))
                except:
                    pass
        return signals
    except:
        return []

def get_name(code):
    """从TOOLS.md获取标的名称"""
    if not os.path.exists(TOOLS_MD):
        return ""
    with open(TOOLS_MD) as f:
        for line in f:
            if code in line:
                m = re.search(r'-\s*(.+?)\s*★', line)
                if m:
                    return m.group(1).strip()
    return ""

def main():
    import argparse
    parser = argparse.ArgumentParser(description='找香农芯创形态标的')
    parser.add_argument('--max-amp', type=float, default=18.0, help='最大振幅%')
    parser.add_argument('--min-conc', type=float, default=75.0, help='最小筹码集中度%')
    parser.add_argument('--min-profit', type=float, default=15.0, help='最小获利盘%')
    parser.add_argument('--max-profit', type=float, default=85.0, help='最大获利盘%')
    parser.add_argument('--all', action='store_true', help='扫描所有缓存标的')
    parser.add_argument('--limit', type=int, default=30)
    args = parser.parse_args()

    # 获取标的列表
    codes = set()
    if args.all:
        for f in os.listdir(CACHE_DIR):
            code = f.split('.')[0]
            if code.isdigit() and len(code) == 6:
                codes.add(code)
    else:
        if os.path.exists(TOOLS_MD):
            with open(TOOLS_MD) as f:
                for line in f:
                    m = re.search(r'(\d{6})\s', line)
                    if m:
                        codes.add(m.group(1))

    print(f"扫描 {len(codes)} 个标的...")
    print(f"条件: 15-40天振幅<{args.max_amp}%, 集中度>{args.min_conc}%, 获利盘{args.min_profit}-{args.max_profit}%")
    print("=" * 80)

    results = []
    for code in sorted(codes):
        rows = load_kline(code)
        if not rows or len(rows) < 20:
            continue
        current = rows[-1]['close']

        # 找横盘窗口
        bw = find_best_window(rows, max_amp=args.max_amp)
        if not bw:
            continue

        # 跑筹码分布
        for ext in ['.day', '.txt', '']:
            cp = f"{CACHE_DIR}/{code}{ext}"
            if os.path.exists(cp):
                break
        signals = run_chip(code, cp, current)

        # 提取低位单峰密集信号
        low_single = None
        profit_high = None
        for s in signals:
            if s['rule'] == 'chip_peak_low_single':
                low_single = s
            elif s['rule'] == 'chip_profit_high':
                profit_high = s

        if not low_single:
            continue

        conc = low_single.get('concentration', 0)
        profit = low_single.get('profit_ratio', 0)
        peak = low_single.get('peak_price', 0)

        if conc < args.min_conc:
            continue
        if profit < args.min_profit or profit > args.max_profit:
            continue

        pos = (current - bw['lo']) / (bw['hi'] - bw['lo']) * 100 if bw['hi'] > bw['lo'] else 50

        results.append({
            'code': code,
            'name': get_name(code),
            'price': current,
            'wlen': bw['wlen'],
            'amp': bw['amp'],
            'total_vol': bw['total_vol'],
            'box': f"{bw['lo']:.0f}-{bw['hi']:.0f}",
            'pos': pos,
            'conc': conc,
            'profit': profit,
            'peak': peak,
            'confidence': low_single.get('confidence', ''),
        })

    # 排序：位置低优先、集中度高优先
    results.sort(key=lambda x: (x['pos'], -x['conc']))

    if results:
        print(f"\n✅ 找到 {len(results)} 个标的:\n")
        print(f"{'代码':>6} {'名称':>10} {'价格':>8} {'天数':>4} {'振幅':>5} {'箱体':>12} {'位置':>5} {'集中度':>5} {'获利%':>5} {'峰价':>8} {'置信'}")
        print("-" * 85)
        for r in results[:args.limit]:
            pos_str = f"{r['pos']:.0f}%"
            if r['pos'] < 30:
                pos_str += " 🔽"
            elif r['pos'] > 70:
                pos_str += " 🔼"
            print(f"{r['code']:>6} {r['name']:>10} {r['price']:>8.2f} {r['wlen']:>4d} {r['amp']:>4.1f}% {r['box']:>12} {pos_str:>7} {r['conc']:>4.0f}% {r['profit']:>4.0f}% {r['peak']:>8.2f} {r['confidence']:>6}")
        print("\n位置标记: 🔽=箱体下沿(低吸区)  🔼=箱体上沿(突破观察)")
    else:
        print(f"\n❌ 当前无符合条件标的")
        # 显示所有低位单峰密集标的（不限横盘）
        print("\n所有低位单峰密集标的（不限横盘）:")
        print(f"{'代码':>6} {'名称':>10} {'价格':>8} {'集中度':>5} {'获利%':>5} {'峰价':>8} {'置信'}")
        print("-" * 55)
        for code in sorted(codes):
            rows = load_kline(code)
            if not rows or len(rows) < 15:
                continue
            current = rows[-1]['close']
            for ext in ['.day', '.txt', '']:
                cp = f"{CACHE_DIR}/{code}{ext}"
                if os.path.exists(cp):
                    break
            signals = run_chip(code, cp, current)
            for s in signals:
                if s['rule'] == 'chip_peak_low_single':
                    conc = s.get('concentration', 0)
                    profit = s.get('profit_ratio', 0)
                    peak = s.get('peak_price', 0)
                    conf = s.get('confidence', '')
                    if conc >= args.min_conc:
                        print(f"{code:>6} {get_name(code):>10} {current:>8.2f} {conc:>4.0f}% {profit:>4.0f}% {peak:>8.2f} {conf:>6}")
                    break

if __name__ == '__main__':
    main()
