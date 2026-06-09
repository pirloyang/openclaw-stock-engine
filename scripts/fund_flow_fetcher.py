#!/usr/bin/env python3
"""
资金流向数据抓取器 V1.0
======================
数据源：证券时报·数据宝（stcn.com）
抓取：从已知URL抓取当日资金流向文章 → 解析结构化数据
输出：/tmp/stock_alerts/fund_flow.json
"""

import json
import os
import re
import sys
from datetime import datetime
from urllib.request import urlopen, Request

OUTPUT_FILE = "/tmp/stock_alerts/fund_flow.json"
HISTORY_FILE = "/root/.openclaw/workspace/data/fund_flow_history.json"
MAX_HISTORY_DAYS = 5


def fetch_text(url):
    try:
        req = Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })
        resp = urlopen(req, timeout=15)
        html = resp.read().decode("utf-8", errors="replace")
        text = re.sub(r'<[^>]+>', '', html)
        text = re.sub(r'\s+', ' ', text)
        return text
    except Exception as e:
        print(f"抓取失败: {e}", file=sys.stderr)
        return None


def parse_market(text):
    result = {}
    m = re.search(r'沪深两市主力资金净流入([\d.]+)亿元', text)
    if m: result['total_inflow'] = float(m.group(1))
    m = re.search(r'创业板净流入([\d.]+)亿元', text)
    if m: result['cyb_inflow'] = float(m.group(1))
    m = re.search(r'沪深300成份股净流入([\d.]+)亿元', text)
    if m: result['hs300_inflow'] = float(m.group(1))
    m = re.search(r'尾盘两市主力资金净流入([\d.]+)亿元', text)
    if m: result['tail_inflow'] = float(m.group(1))
    return result


def parse_sectors(text):
    sectors = {"inflow_top": [], "outflow_top": []}
    for m in re.finditer(r'([\u4e00-\u9fa5]{2,6})行业主力资金净流入([\d.]+)亿元', text):
        name = m.group(1)
        if name not in ('沪深两市', '尾盘两市'):
            sectors['inflow_top'].append({"name": name, "amount": float(m.group(2))})
    for m in re.finditer(r'([\u4e00-\u9fa5]{2,6})行业(?:主力资金)?净流出金额居首，达([\d.]+)亿元', text):
        sectors['outflow_top'].append({"name": m.group(1), "amount": -float(m.group(2))})
    for m in re.finditer(r'([\u4e00-\u9fa5]{2,6})行业主力资金净流出([\d.]+)亿元', text):
        name = m.group(1)
        if not any(s['name'] == name for s in sectors['outflow_top']):
            sectors['outflow_top'].append({"name": name, "amount": -float(m.group(2))})
    return sectors


def is_valid_stock_name(name):
    """判断是否为有效的个股名称（排除板块名、杂音）"""
    invalid = {'电子', '通信', '机械设备', '电力设备', '基础化工', '有色金属', '建筑材料',
               '沪深两市', '尾盘两市', '创业板', '沪深300', '综合', '建筑装饰',
               '医药生物', '煤炭', '家用电器', '银行', '国防军工', '环保',
               '石油石化', '美容护理', '食品饮料', '商贸零售', '农林牧渔',
               '股价', '龙头股', '热门股', '新股'}
    if name in invalid:
        return False
    if any(w in name for w in ['行业', '龙头', '尾盘', '股价', '模块', '新股', '股新']):
        return False
    if len(name) < 2 or len(name) > 6:
        return False
    return True


def parse_stocks(text):
    stocks = {"inflow_top": [], "outflow_top": [], "tail_inflow": []}

    # 格式1: XXX上涨X%，主力资金净流入X亿元
    for m in re.finditer(r'([\u4e00-\u9fa5]{2,6})上涨[\d.]+\%[，,][^。]*?主力资金净流入([\d.]+)亿元', text):
        name = m.group(1)
        if is_valid_stock_name(name):
            stocks['inflow_top'].append({"name": name, "amount": float(m.group(2))})

    # 格式2: XXX主力资金净流入居首，金额达X亿元（短名称）
    for m in re.finditer(r'([\u4e00-\u9fa5]{2,4})主力资金净流入居首[^。]*?金额达([\d.]+)亿元', text):
        name = m.group(1)
        if is_valid_stock_name(name):
            stocks['inflow_top'].append({"name": name, "amount": float(m.group(2))})

    # 格式3: 通用匹配（仅限2-4字，排除板块）
    for m in re.finditer(r'([\u4e00-\u9fa5]{2,4})主力资金净流入([\d.]+)亿元', text):
        name = m.group(1)
        if is_valid_stock_name(name) and not any(s['name'] == name for s in stocks['inflow_top']):
            stocks['inflow_top'].append({"name": name, "amount": float(m.group(2))})

    # 格式4: XXX主力资金净流入最多/居前，净流入金额为X亿元
    for m in re.finditer(r'([\u4e00-\u9fa5]{2,6})主力资金净流入(?:最多|居前)[^。]*?净流入金额为([\d.]+)亿元', text):
        name = m.group(1)
        if is_valid_stock_name(name) and not any(s['name'] == name for s in stocks['inflow_top']):
            stocks['inflow_top'].append({"name": name, "amount": float(m.group(2))})

    # 个股净流出列表
    m_list = re.search(r'([\u4e00-\u9fa5A-Z\-、和]+)等热门股主力资金净流出居前，均超([\d.]+)亿元', text)
    if m_list:
        names = re.split(r'[、和]', m_list.group(1))
        amount = float(m_list.group(2))
        for n in names:
            n = n.strip()
            if n and is_valid_stock_name(n):
                stocks['outflow_top'].append({"name": n, "amount": -amount})

    # 尾盘净流入
    for m in re.finditer(r'([\u4e00-\u9fa5]{2,6})[\u3001、，,]\s*(?:尾盘)?主力资金净流入([\d.]+)亿元', text):
        name = m.group(1).replace('涨停', '').replace('跌停', '').strip()
        if is_valid_stock_name(name) and name not in ('创业板', '沪深300'):
            stocks['tail_inflow'].append({"name": name, "amount": float(m.group(2))})

    # 去重
    for key in ['inflow_top', 'outflow_top', 'tail_inflow']:
        seen = set()
        unique = []
        for item in stocks[key]:
            if item['name'] not in seen:
                seen.add(item['name'])
                unique.append(item)
        stocks[key] = unique

    return stocks


def build_code_map():
    signals_file = "/tmp/stock_alerts/engine_signals.json"
    if not os.path.exists(signals_file):
        return {}
    try:
        with open(signals_file) as f:
            signals = json.load(f)
    except:
        return {}
    code_map = {}
    for s in signals if isinstance(signals, list) else []:
        name = s.get('name', '')
        code = s.get('code', '')
        if name and code:
            code_map[name] = code
    return code_map


def enrich_with_codes(data, code_map):
    for category in ['inflow_top', 'outflow_top', 'tail_inflow']:
        for item in data.get('stocks', {}).get(category, []):
            name = item['name']
            if name in code_map:
                item['code'] = code_map[name]
            else:
                for n, c in code_map.items():
                    if name in n or n in name:
                        item['code'] = c
                        break
                if 'code' not in item:
                    item['code'] = ''
    return data


def save_history(data):
    history = []
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE) as f:
                history = json.load(f)
        except:
            pass
    today = datetime.now().strftime("%Y-%m-%d")
    history = [h for h in history if h.get('date') != today]
    history.append({
        'date': today,
        'market': data.get('market', {}),
        'sectors': data.get('sectors', {}),
    })
    history = history[-MAX_HISTORY_DAYS:]
    os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
    with open(HISTORY_FILE, 'w') as f:
        json.dump(history, f, indent=2, ensure_ascii=False)


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else None

    if not url:
        empty = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "source": "",
            "market": {},
            "sectors": {"inflow_top": [], "outflow_top": []},
            "stocks": {"inflow_top": [], "outflow_top": [], "tail_inflow": []},
        }
        os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
        with open(OUTPUT_FILE, 'w') as f:
            json.dump(empty, f, indent=2, ensure_ascii=False)
        print(OUTPUT_FILE)
        return

    print(f"抓取: {url}", file=sys.stderr)
    text = fetch_text(url)
    if not text:
        return

    market = parse_market(text)
    sectors = parse_sectors(text)
    stocks = parse_stocks(text)
    code_map = build_code_map()

    data = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source": url,
        "market": market,
        "sectors": sectors,
        "stocks": stocks,
    }
    data = enrich_with_codes(data, code_map)

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    save_history(data)

    # 摘要
    print(f"\n资金流向 | {data['date']}", file=sys.stderr)
    if market.get('total_inflow'):
        s = '+' if market['total_inflow'] > 0 else ''
        print(f"  大盘: {s}{market['total_inflow']:.1f}亿", file=sys.stderr)
    if sectors.get('inflow_top'):
        _s = [f"{s['name']}+{s['amount']:.0f}亿" for s in sectors['inflow_top'][:3]]
        print(f"  流入板块: {', '.join(_s)}", file=sys.stderr)
    if sectors.get('outflow_top'):
        _s = [f"{s['name']}{s['amount']:.0f}亿" for s in sectors['outflow_top'][:3]]
        print(f"  流出板块: {', '.join(_s)}", file=sys.stderr)
    if stocks.get('inflow_top'):
        _s = [f"{s['name']}+{s['amount']:.0f}亿" for s in stocks['inflow_top'][:5]]
        print(f"  个股流入: {', '.join(_s)}", file=sys.stderr)
    if stocks.get('outflow_top'):
        _s = [f"{s['name']}{s['amount']:.0f}亿" for s in stocks['outflow_top'][:5]]
        print(f"  个股流出: {', '.join(_s)}", file=sys.stderr)

    print(f"已保存到 {OUTPUT_FILE}", file=sys.stderr)
    print(OUTPUT_FILE)


if __name__ == "__main__":
    main()
