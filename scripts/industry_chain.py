#!/usr/bin/env python3
"""
产业链传导模型 V1.0
- 基于板块强度数据，预制产业链上下游传导规则
- 当上游板块启动时，自动预警下游传导机会
- 输出到 /tmp/stock_alerts/chain_alerts.json
"""
import json, os, sys
from datetime import datetime

WORKSPACE = "/root/.openclaw/workspace"
SECTOR_FLOW_FILE = "/tmp/stock_alerts/sector_flow.json"
CHAIN_MAP_FILE = f"{WORKSPACE}/stock-signals/industry_chain_map.json"
OUTPUT_FILE = "/tmp/stock_alerts/chain_alerts.json"

# 默认产业链传导规则（如果没有 chain_map.json）
DEFAULT_CHAINS = [
    {
        "name": "AI算力链",
        "stages": [
            {"order": 1, "name": "芯片设计", "sectors": ["半导体/芯片", "HBM/存储"], "delay_days": 0},
            {"order": 2, "name": "封装测试", "sectors": ["半导体/芯片"], "delay_days": 3},
            {"order": 3, "name": "PCB/载板",  "sectors": ["PCB/覆铜板"],         "delay_days": 5},
            {"order": 4, "name": "光模块/CPO", "sectors": ["CPO/光通信"],         "delay_days": 7},
            {"order": 5, "name": "服务器/IDC",  "sectors": ["半导体/芯片"],         "delay_days": 10},
        ],
        "description": "AI需求→芯片设计先行→封测跟进→PCB/光模块需求→服务器交付"
    },
    {
        "name": "新能源链",
        "stages": [
            {"order": 1, "name": "上游资源", "sectors": ["新能源"],      "delay_days": 0},
            {"order": 2, "name": "中游材料", "sectors": ["新能源"],      "delay_days": 3},
            {"order": 3, "name": "下游制造", "sectors": ["新能源"],      "delay_days": 7},
        ],
        "description": "锂价/硅料→正极/电解液→电池/组件"
    },
    {
        "name": "半导体设备→材料链",
        "stages": [
            {"order": 1, "name": "晶圆厂扩产", "sectors": ["半导体/芯片"], "delay_days": 0},
            {"order": 2, "name": "设备采购",   "sectors": ["半导体/芯片"], "delay_days": 3},
            {"order": 3, "name": "材料需求",   "sectors": ["半导体/芯片"], "delay_days": 7},
        ],
        "description": "晶圆厂扩产→设备先行→材料跟进"
    },
    {
        "name": "航天军工链",
        "stages": [
            {"order": 1, "name": "政策/发射", "sectors": ["商业航天"],    "delay_days": 0},
            {"order": 2, "name": "卫星制造",  "sectors": ["商业航天"],    "delay_days": 2},
            {"order": 3, "name": "地面设备",  "sectors": ["商业航天"],    "delay_days": 5},
            {"order": 4, "name": "运营服务",  "sectors": ["商业航天"],    "delay_days": 8},
        ],
        "description": "发射任务→卫星制造先行→地面设备→数据服务"
    },
]

def load_sector_flow():
    if not os.path.exists(SECTOR_FLOW_FILE):
        return None
    try:
        with open(SECTOR_FLOW_FILE) as f:
            return json.load(f)
    except:
        return None

def load_chain_map():
    if os.path.exists(CHAIN_MAP_FILE):
        try:
            with open(CHAIN_MAP_FILE) as f:
                return json.load(f)
        except:
            pass
    return DEFAULT_CHAINS

def check_chain_activation(sector_data, chain):
    """检查产业链上游是否已激活"""
    stages = chain['stages']
    activated_stages = []
    next_stage_alerts = []
    
    for i, stage in enumerate(stages):
        is_active = False
        for sec in stage['sectors']:
            sec_info = sector_data.get(sec, {})
            if sec_info.get('level') in ['A', 'B']:
                is_active = True
                break
        
        if is_active:
            activated_stages.append({
                'name': stage['name'],
                'sectors': stage['sectors'],
                'order': stage['order']
            })
            
            # 检查下一环节是否需要预警
            if i + 1 < len(stages):
                next_stage = stages[i + 1]
                # 下一环节的板块是否尚未启动
                next_active = False
                for ns in next_stage['sectors']:
                    ns_info = sector_data.get(ns, {})
                    if ns_info.get('level') in ['A', 'B']:
                        next_active = True
                        break
                
                if not next_active:
                    next_stage_alerts.append({
                        'chain': chain['name'],
                        'activated_stage': stage['name'],
                        'next_stage': next_stage['name'],
                        'next_sectors': next_stage['sectors'],
                        'expected_delay': next_stage['delay_days'],
                        'message': f"{chain['name']}: {stage['name']}已启动→关注{next_stage['name']}({', '.join(next_stage['sectors'])})，预计{next_stage['delay_days']}日内传导"
                    })
    
    return {
        'chain_name': chain['name'],
        'activated_count': len(activated_stages),
        'activated_stages': activated_stages,
        'pending_alerts': next_stage_alerts,
        'description': chain['description']
    }

def main():
    flow_data = load_sector_flow()
    if not flow_data:
        print("⚠️ 无板块数据，请先运行 sector_fund_flow.py")
        sys.exit(1)
    
    sector_data = flow_data.get('sectors', {})
    chains = load_chain_map()
    
    chain_results = []
    all_alerts = []
    
    for chain in chains:
        result = check_chain_activation(sector_data, chain)
        chain_results.append(result)
        all_alerts.extend(result['pending_alerts'])
    
    # 按紧急程度排序
    all_alerts.sort(key=lambda x: x['expected_delay'])
    
    output = {
        'updated': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'chain_results': chain_results,
        'active_alerts': all_alerts[:5],  # 最多5条
    }
    
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    
    print(f"🔗 产业链传导 | {datetime.now().strftime('%H:%M')}")
    for cr in chain_results:
        if cr['activated_count'] > 0:
            stages = ' → '.join(s['name'] for s in cr['activated_stages'])
            print(f"  [{cr['chain_name']}] 已激活: {stages}")
    
    if all_alerts:
        print(f"\n🎯 传导机会预警:")
        for a in all_alerts[:5]:
            print(f"  ⏳ {a['message']}")

if __name__ == "__main__":
    main()
