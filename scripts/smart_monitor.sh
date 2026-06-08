#!/bin/bash
# ==========================================================
# 智能监控推送 V5 — 大盘优先+板块热力+解盘版
# 输出结构：
#   大盘环境（四指数+趋势判断）
#   板块热力（ETF+概念）
#   持仓异动（含解盘+价格）
#   机会信号（L2/L3，含价格点）
# ==========================================================

WORKSPACE="/root/.openclaw/workspace"
ENGINE="$WORKSPACE/stock-signals/engine.sh"

# ====== 1. 先拉大盘和板块数据 ======
IDX_RAW=$(curl -s --max-time 5 "https://qt.gtimg.cn/q=sh000001,sz399001,sz399006,sh000688" 2>/dev/null | iconv -f GBK -t UTF-8 | sed 's/";v_/";\nv_/g')
ETF_RAW=$(curl -s --max-time 5 "https://qt.gtimg.cn/q=sh516640,sz159667,sz159858,sz159928,sh512400" 2>/dev/null | iconv -f GBK -t UTF-8 | sed 's/";v_/";\nv_/g')

get_idx() {
  echo "$IDX_RAW" | grep -m1 "$1" | awk -F'~' -v f="$2" '{print $f}'
}

SH=$(get_idx "sh000001" 4)   # 当前
SH_CHG=$(get_idx "sh000001" 33 | tr -d '%')
SH_HIGH=$(get_idx "sh000001" 34)
SH_LOW=$(get_idx "sh000001" 35)
SZ=$(get_idx "sz399001" 4)
SZ_CHG=$(get_idx "sz399001" 33 | tr -d '%')
CY=$(get_idx "sz399006" 4)
CY_CHG=$(get_idx "sz399006" 33 | tr -d '%')
KCB=$(get_idx "sh000688" 4)
KCB_CHG=$(get_idx "sh000688" 33 | tr -d '%')

# 趋势判断（自然语言）
trend_judge() {
  local sh="$1" kcb="$2" sz="$3" cy="$4"
  local shf=$(echo "$sh" | awk '{print ($1>0)?1:0}')
  local kcbf=$(echo "$kcb" | awk '{print ($1>0)?1:0}')
  local szf=$(echo "$sz" | awk '{print ($1>0)?1:0}')
  local cyf=$(echo "$cy" | awk '{print ($1>0)?1:0}')
  local ups=$((shf + kcbf + szf + cyf))
  
  if [ "$ups" -ge 3 ]; then
    echo "四指数三红以上，市场偏强，资金活跃度中等偏高"
  elif [ "$ups" -ge 2 ]; then
    echo "指数分化，科创领涨，结构行情为主"
  elif [ "$kcbf" -eq 1 ]; then
    echo "仅科创收红，科技方向有资金抱团，其他板块偏弱"
  else
    echo "全市场偏弱，谨慎观望为主"
  fi
}

TREND=$(trend_judge "$SH_CHG" "$KCB_CHG" "$SZ_CHG" "$CY_CHG")

# 板块热力
ETF_DATA=""
while IFS= read -r line; do
  name=$(echo "$line" | awk -F'~' '{print $2}')
  chg=$(echo "$line" | awk -F'~' '{print $33}' | tr -d '%')
  price=$(echo "$line" | awk -F'~' '{print $4}')
  [ -z "$chg" ] && continue
  abs=$(echo "$chg" | sed 's/^-//')
  tag=""
  [ "$(echo "$abs > 2.5" | bc -l 2>/dev/null)" = "1" ] && tag="⚡"
  [ "$(echo "$abs > 5" | bc -l 2>/dev/null)" = "1" ] && tag="🔥"
  [ -n "$ETF_DATA" ] && ETF_DATA="${ETF_DATA} | "
  ETF_DATA="${ETF_DATA}${name} ${price}(${chg}%)${tag}"
done <<< "$ETF_RAW"

# ====== 2. 运行信号引擎 ======
HOLDINGS_RAW=$(bash "$WORKSPACE/scripts/tools.sh" holdings 2>/dev/null)
HOLD_CODES=$(echo "$HOLDINGS_RAW" | awk '{print $1}' | tr '\n' ' ')
ENGINE_OUTPUT=$("$ENGINE" 2>/dev/null)
SIGNAL_FILE=$(echo "$ENGINE_OUTPUT" | grep '^signal_file=' | cut -d= -f2)
[ ! -f "$SIGNAL_FILE" ] && { echo "⚠️ 引擎无信号"; exit 0; }

DEDUPED_FILE=$(python3 "$WORKSPACE/stock-signals/signal_dedup.py" filter "$SIGNAL_FILE" 2>/dev/null)
if [ -f "$DEDUPED_FILE" ] && [ -s "$DEDUPED_FILE" ]; then
  SIGNAL_FILE="$DEDUPED_FILE"
fi

# ====== 3. Python信号处理 ======
OUTPUT=$(python3 - "$HOLD_CODES" "$HOLDINGS_RAW" "$SIGNAL_FILE" << 'PYEOF'
import sys, json
from datetime import datetime

holding_codes = set(sys.argv[1].split())
holdings_raw = sys.argv[2].strip()
sig_file = sys.argv[3]

holdings_info = {}
for line in holdings_raw.split('\n'):
    parts = line.strip().split()
    if len(parts) >= 4:
        holdings_info[parts[0]] = (parts[1], int(parts[2]), float(parts[3]))

with open(sig_file) as f:
    data = json.load(f)

holding_alerts = []
buy_alerts = []

for s in data:
    code = s['code']
    level = s.get('price_level', 'L0')
    verdict = s.get('resonance', {}).get('verdict', '观望')
    change = float(s.get('change_pct', '0').replace('%', ''))
    abs_ch = abs(change)
    is_holding = code in holding_codes
    price = float(s['price'])
    try: open_f = float(s.get('open', 0))
    except: open_f = price
    name = s['name']
    sigs = s.get('signals', [])
    ma5 = s.get('ma5', 'n/a')
    ma20 = s.get('ma20', 'n/a')

    # 提取关键信号描述
    evidence = ''
    for sig in sigs[:3]:
        note = sig.get('note', '')
        rule = sig.get('rule', '')
        if any(k in rule for k in ['ma_death','ma_golden','macd_top','macd_bottom','bullish_arr','bearish_arr','breakout','breakdown','volume_surge','volume_shrink','volume_pullback','shrink_reversal','should_rise','should_fall','hammer','doji','red_three','three_crows','rsi','gap','trailing']):
            evidence = note
            break
    if not evidence and sigs:
        evidence = sigs[0].get('note', '')

    # --- 持仓处理（全部列出，有信号就分析） ---
    if is_holding:
        info = holdings_info.get(code)
        if not info:
            continue
        _, shares, cost = info
        pl = round((price - cost) * shares)
        pl_sign = '+' if pl >= 0 else ''
        pl_pct = (price - cost) / cost * 100

        # 位置判断
        pos_desc = ''
        if ma20 != 'n/a' and ma5 != 'n/a':
            ma20f = float(ma20)
            ma5f = float(ma5)
            if price > ma5f > ma20f:
                pos_desc = f"站上MA5({ma5f:.2f})和MA20({ma20f:.2f})，多头排列"
            elif ma5f > price > ma20f:
                pos_desc = f"在MA5({ma5f:.2f})下方但守在MA20({ma20f:.2f})之上，短线偏弱但中期趋势未破"
            elif price > ma20f:
                pos_desc = f"在MA20({ma20f:.2f})上方，中期趋势完好"
            else:
                pos_desc = f"跌破MA20({ma20f:.2f})，趋势转弱"
        else:
            pos_desc = "位置待确认"

        # 定性分析
        if '卖出' in verdict:
            quality = "技术面有转弱信号，注意风险"
        elif '三重共振' in verdict or '双重确认' in verdict:
            quality = "技术面偏强，持有为主"
        else:
            quality = "趋势正常，观望"

        # 止损价
        stop_price = round(cost * 0.95, 2)

        # 输出
        out = f"{name}({code}) {price}元 {change:+.2f}%"
        out += f" | 持仓{shares}股@{cost:.2f} 浮盈{pl_sign}{pl}({pl_pct:+.2f}%)"
        out += f"\n   {pos_desc}。{quality}。{evidence}"
        out += f"\n   → 止损¥{stop_price}（成本-5%）"
        holding_alerts.append(out)

    # --- 机会信号（L2/L3非持仓，上涨中） ---
    else:
        is_L2_L3 = abs_ch >= 4 and level in ('L2_STRONG', 'L3_URGENT')
        is_triple = verdict == '三重共振-出手' and abs_ch >= 2
        is_sell = '卖' in verdict

        if not (is_L2_L3 or is_triple):
            continue
        if is_sell:
            continue
        if level in ('L0_NORMAL', 'L1_NORMAL'):
            continue
        if change < 0:
            continue

        # 位置判断
        if ma20 != 'n/a' and ma5 != 'n/a':
            ma20f = float(ma20) if ma20 != 'n/a' else 0
            ma5f = float(ma5) if ma5 != 'n/a' else 0
            gap20 = (price - ma20f) / ma20f * 100 if ma20f > 0 else 0
            gap5 = (price - ma5f) / ma5f * 100 if ma5f > 0 else 0
            if gap20 > 30:
                pos = f"偏离MA20达{gap20:.0f}%高位，距{ma5f:.2f}还差{gap5:.0f}%，等回踩再进"
                buy_zone = f"MA5({ma5f:.2f})附近"
            elif gap20 > 15:
                pos = f"偏离MA20约{gap20:.0f}%的强势区，回踩MA5({ma5f:.2f})是买点"
                buy_zone = f"回踩MA5({ma5f:.2f})"
            else:
                pos = f"距MA20仅{gap20:.0f}%的中低位，突破后回踩MA5({ma5f:.2f})入场"
                buy_zone = f"回踩MA5({ma5f:.2f})"
            stop = round(open_f * 0.95, 2) if open_f > 0 else round(price * 0.95, 2)
        else:
            pos = "位置待确认"
            buy_zone = "等回调企稳"
            stop = round(price * 0.93, 2)

        emoji = '🔴🔴' if abs_ch > 7 else ('⚡⚡' if abs_ch >= 4 else '⚡')
        out = f"{name}({code}) {price}元 {change:+.2f}% {emoji}"
        out += f"\n   {pos}。{evidence}"
        out += f"\n   → 介入：{buy_zone}入场100股。止损¥{stop}"
        buy_alerts.append(out)

# --- 形态信号（全池扫描，pattern规则→自然语言） ---
PATTERN_MAP = {
    'volume_shrink':       ('🔷', '缩量洗盘', 4),
    'volume_surge':        ('🔴', '放量异动', 3),
    'turnover_abnormal':   ('🔄', '换手率过高', 3),
    'turnover_high':       ('🔄', '换手率活跃', 2),
    'red_three':           ('🔴🔴', '红三兵启动', 5),
    'three_crows':         ('⬇⬇⬇', '三只乌鸦下跌', 4),
    'hammer':              ('🔨', '锤子线底部确认', 3),
    'hanging_man':         ('⚠️', '上吊线高位预警', 4),
    'shooting_star':       ('🌠', '射击之星高位预警', 5),
    'fairy_guide':         ('🧚', '仙人指路中继看涨', 4),
    'morning_star':        ('🌅', '早晨之星底部反转', 5),
    'upper_wick':          ('▲', '倒锤线长上影', 2),
    'gap_up':              ('⬆', '向上跳空', 3),
    'gap_down':            ('⬇', '向下跳空', 3),
    'breakout_up':         ('🔥', '放量突破', 5),
    'breakdown':           ('💥', '跌破支撑', 4),
    'should_rise_fail':    ('⚠️⚠️', '该涨不涨', 5),
    'should_fall_strong':  ('✅', '该跌不跌强势抗跌', 4),
    'trailing_stop':       ('🪙', '动态止盈', 3),
    'trailing_stop_urgent':('🪙🪙', '动态止盈-紧急', 5),
    'volume_pullback_support':('🛡️🛡️', '缩量回踩支撑+主力未走', 5),
    'shrink_reversal':      ('🔵🔵', '缩量见底+放量反包', 5),
    'entry_stop_loss':     ('🛡️', '入场止损位', 3),
    'doji':                ('—', '十字星多空均衡', 1),
}

# 按重要性等级排序的规则分组
HIGH_IMPORTANCE = {k for k,v in PATTERN_MAP.items() if v[2] >= 4}
MEDIUM_IMPORTANCE = {k for k,v in PATTERN_MAP.items() if v[2] == 3}
LOW_IMPORTANCE = {k for k,v in PATTERN_MAP.items() if v[2] <= 2}

pattern_by_code = {}  # code -> {name, price, change, patterns:[(emoji, desc, note)]}
for s in data:
    code = s['code']
    name = s['name']
    price = float(s['price'])
    change = float(s.get('change_pct', '0').replace('%', ''))
    sigs = s.get('signals', [])
    
    for sig in sigs:
        rule = sig.get('rule', '')
        if rule in PATTERN_MAP:
            info = pattern_by_code.setdefault(code, {'name':name, 'price':price, 'change':change, 'patterns':[], 'max_imp':0})
            emoji, desc, imp = PATTERN_MAP[rule]
            note = sig.get('note', '')
            info['patterns'].append((emoji, desc, note, imp))
            if imp > info['max_imp']:
                info['max_imp'] = imp

pattern_high = []
pattern_med = []
for code, info in pattern_by_code.items():
    check_code = code
    is_holding_check = code in holding_codes
    group = pattern_high if info['max_imp'] >= 4 else pattern_med
    # 形态描述
    pattern_descs = []
    for emoji, desc, note, imp in info['patterns']:
        pattern_descs.append(f"{emoji}{desc}")
    parts = list(set(pattern_descs))
    parts.sort()
    detail = ' | '.join(parts)
    
    chg_str = f"{info['change']:+.2f}%"
    data_words = detail.lower()
    raw_note = '; '.join(set([n for _,_,n,_ in info['patterns'] if n]))
    
    out = f"{info['name']}({code}) {info['price']}元 {chg_str}\n   {detail}"
    if raw_note:
        # 去冗：相同note不重复
        uniq_notes = []
        for n in [x[2] for x in info['patterns']]:
            if n and n not in uniq_notes:
                uniq_notes.append(n)
        if uniq_notes:
            out += f"\n   {' '.join(uniq_notes)}"
    group.append(out)

pattern_alerts = []
if pattern_high:
    pattern_alerts.append("【形态】⚠️ 高优先级")
    pattern_alerts.extend(pattern_high)
if pattern_med:
    if pattern_alerts:
        pattern_alerts.append("")
    pattern_alerts.append("【形态】🔷 观察级")
    pattern_alerts.extend(pattern_med)

output_lines = []

# 大盘环境（bash已输出，Python只输出信号部分）

# 持仓信号
if holding_alerts:
    output_lines.append("【持仓】")
    output_lines.extend(holding_alerts)

# 机会信号
if buy_alerts:
    output_lines.append("")
    output_lines.append("【机会】")
    output_lines.extend(buy_alerts)

# 形态信号
if pattern_alerts:
    if holding_alerts or buy_alerts:
        output_lines.append("")
    output_lines.extend(pattern_alerts)

if not holding_alerts and not buy_alerts and not pattern_alerts:
    output_lines.append("全池安静，无介入级别信号")

print('\n'.join(output_lines))
PYEOF
)

# ====== 4. 组装最终输出 ======
echo "════════════════════════════════════"
echo "📊 $(date '+%H:%M') 盘中扫描"
echo "════════════════════════════════════"

# 大盘
sh_arrow=""; [ "$(echo "$SH_CHG > 0" | bc -l 2>/dev/null)" = "1" ] && sh_arrow="↑" || sh_arrow="↓"
[ "$SH_CHG" = "0.00" ] && sh_arrow="→"
kcb_arrow=""; [ "$(echo "$KCB_CHG > 0" | bc -l 2>/dev/null)" = "1" ] && kcb_arrow="↑" || kcb_arrow="↓"
[ "$KCB_CHG" = "0.00" ] && kcb_arrow="→"
sz_arrow=""; [ "$(echo "$SZ_CHG > 0" | bc -l 2>/dev/null)" = "1" ] && sz_arrow="↑" || sz_arrow="↓"
cy_arrow=""; [ "$(echo "$CY_CHG > 0" | bc -l 2>/dev/null)" = "1" ] && cy_arrow="↑" || cy_arrow="↓"

echo "【大盘】上证${SH} ${sh_arrow}(${SH_CHG}%) 区间${SH_LOW}-${SH_HIGH} | 深证${SZ} ${sz_arrow}(${SZ_CHG}%) | 创业板${CY} ${cy_arrow}(${CY_CHG}%) | 科创50${KCB} ${kcb_arrow}(${KCB_CHG}%)"
echo "→ ${TREND}"

# 板块
echo "【板块】$ETF_DATA"

# 信号
echo ""
echo "$OUTPUT"

echo ""
echo "════════════════════════════════════"

# 清理
if [ -n "$DEDUPED_FILE" ] && [ "$SIGNAL_FILE" = "$DEDUPED_FILE" ]; then
  ORIG_FILE=$(echo "$SIGNAL_FILE" | sed 's/_dedup\.json$/.json/')
  rm -f "$ORIG_FILE" "$SIGNAL_FILE"
else
  rm -f "$SIGNAL_FILE"
fi
