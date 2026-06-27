#!/bin/bash
# ===== awk 辅助函数（替代 bc -l，v6.5 性能优化）=====
# awk 启动速度比 bc 快 10x+，且原生支持浮点
calc() { awk "BEGIN{v=($1); if(v==int(v)) printf \"%d\", v; else printf \"%.4f\", v}" 2>/dev/null; }
calc_scale() { awk "BEGIN{printf \"%.${2:-4}f\", $1}" 2>/dev/null; }
cmp() { [ "$(awk "BEGIN{printf \"%d\", ($1) ? 1 : 0}")" = "1" ]; }
# ==========================================================
# 信号引擎 V3 — 数据预取架构
# 1. 一次拉取全池行情 (gtimg 单次 curl)
# 2. 从缓存计算所有衍生指标（MA/MACD/量比）
# 3. 每条规则收到完整数据 struct（零网络调用）
# ==========================================================

SIGNAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RULES_DIR="$SIGNAL_DIR/rules"
CACHE_DIR="$SIGNAL_DIR/cache"
WORKSPACE="/root/.openclaw/workspace"
mkdir -p "$CACHE_DIR"

# --------------- 数据预取层 ---------------

get_all_codes() {
  # 合并所有采集来源，经过去重
  # 主数据源：focus_watchlist.json（辉哥可编辑，不硬编码）
  {
    printf "000001\n399001\n399006\n"
    bash "$WORKSPACE/scripts/tools.sh" holdings 2>/dev/null | awk '{print $1}'
    bash "$WORKSPACE/scripts/tools.sh" history  2>/dev/null | awk '{print $1}'
    # 从 focus_watchlist.json 动态读取自选池
    python3 -c "
import json, sys
try:
    with open('$WORKSPACE/stock-signals/focus_watchlist.json') as f:
        data = json.load(f)
    meta = {'focus_list','last_update','version'}
    for c in sorted(data):
        if c not in meta:
            print(c)
except: pass
" 2>/dev/null
  } | sort -u | grep -v '^$'
}

fetch_market() {
  local raw=$(curl -s --max-time 5 "https://qt.gtimg.cn/q=sh000001,sz399006" 2>/dev/null | iconv -f GBK -t UTF-8)
  # 确保每行一个变量（gtimg多股查询在iconv后可能合并为一行）
  raw=$(echo "$raw" | sed 's/";v_/";\nv_/g')
  local sh=$(echo "$raw" | grep "sh000001" | awk -F'~' '{print $33}' | tr -d '%')
  local cy=$(echo "$raw" | grep "sz399006" | awk -F'~' '{print $33}' | tr -d '%')
  echo "${sh:-0}|${cy:-0}"
}

fetch_bulk() {
  # 分批拉取全池实时行情（gtimg 大请求易超时，每批30只）
  local batch="" count=0 result=""
  while read code; do
    [ -z "$code" ] && continue
    [[ $code == 6* || $code == "000001" ]] && batch="${batch}sh${code}," || batch="${batch}sz${code},"
    ((count++))
    if [ $count -ge 30 ]; then
      local chunk=$(curl -s --max-time 15 "https://qt.gtimg.cn/q=${batch%,}" 2>/dev/null)
      result="${result}${chunk}"
      batch="" count=0
    fi
  done < <(get_all_codes)
  if [ -n "$batch" ]; then
    local chunk=$(curl -s --max-time 15 "https://qt.gtimg.cn/q=${batch%,}" 2>/dev/null)
    result="${result}${chunk}"
  fi
  echo "$result" | iconv -f GBK -t UTF-8 2>/dev/null | sed 's/";v_/";\nv_/g'
}

# 均线计算（6字段格式: close open high low vol date）
ma_n() { local f="$1" n="$2"; [ -f "$f" ] && [ "$(wc -l < "$f")" -ge "$n" ] && tail -"$n" "$f" | awk '{s+=$1} END{printf "%.2f", s/'$n'}'; }

# 均成交量（6字段格式: $5=vol）
avgvol_n() { local f="$1" n="$2"; [ -f "$f" ] && [ "$(wc -l < "$f")" -ge "$n" ] && tail -"$n" "$f" | awk '{s+=$5} END{printf "%.0f", s/'$n'}'; }

# 近N日最高/最低价（6字段格式: $3=high, $4=low）
high_n() { local f="$1" n="$2"; [ -f "$f" ] && [ "$(wc -l < "$f")" -ge "$n" ] && tail -"$n" "$f" | awk 'max==""||$3>max{max=$3} END{printf "%.2f", max}'; }
# 排除当天（最后1行）的近N日最高价 — 避免冲高回落当天HH被当日盘中高点污染误杀
high_n_excl_today() { local f="$1" n="$2"; [ -f "$f" ] && [ "$(wc -l < "$f")" -gt "$n" ] && tail -"$n" "$f" | head -"$((n-1))" | awk 'max==""||$3>max{max=$3} END{printf "%.2f", max}'; }
low_n()  { local f="$1" n="$2"; [ -f "$f" ] && [ "$(wc -l < "$f")" -ge "$n" ] && tail -"$n" "$f" | awk 'min==""||$4<min{min=$4} END{printf "%.2f", min}'; }

# EMA (指数移动平均) — 用于 MACD
# v5.3: 全序列递推EMA，修复v5.2只取最后N行导致的DIF计算错误
# v6.4: 批量计算，一次python3处理所有标的，避免每只启动子进程
calc_ema_batch() {
  local cache_dir="$1"
  # 输出文件: code|ema12|ema26|prev_dif
  python3 -c "
import os, sys

cache_dir = '$cache_dir'
results = {}

for fname in os.listdir(cache_dir):
    if not fname.endswith('.day'):
        continue
    code = fname[:-4]
    fpath = os.path.join(cache_dir, fname)
    with open(fpath) as f:
        lines = f.readlines()
    
    prices = []
    for l in lines:
        parts = l.split()
        if parts:
            try:
                prices.append(float(parts[0]))
            except:
                pass
    
    if len(prices) < 26:
        continue
    
    # ema12
    ema = prices[0]
    k = 2/13
    for p in prices[1:]:
        ema = p*k + ema*(1-k)
    ema12 = ema
    
    # ema26
    ema = prices[0]
    k = 2/27
    for p in prices[1:]:
        ema = p*k + ema*(1-k)
    ema26 = ema
    
    dif = ema12 - ema26
    
    # prev_dif: 去掉最后一行
    if len(prices) >= 27:
        prev_prices = prices[:-1]
        ema = prev_prices[0]
        k = 2/13
        for p in prev_prices[1:]:
            ema = p*k + ema*(1-k)
        prev_ema12 = ema
        
        ema = prev_prices[0]
        k = 2/27
        for p in prev_prices[1:]:
            ema = p*k + ema*(1-k)
        prev_ema26 = ema
        prev_dif = prev_ema12 - prev_ema26
    else:
        prev_dif = dif
    
    results[code] = (f'{ema12:.2f}', f'{ema26:.2f}', f'{dif:.2f}', f'{prev_dif:.2f}')

for code, (e12, e26, d, pd) in results.items():
    print(f'{code}|{e12}|{e26}|{d}|{pd}')
" 2>/dev/null
}

# 从批量结果中查找指定code的MACD值
# 全局变量 MACD_BATCH_RESULT 由 precompute_macd 填充
MACD_BATCH_RESULT=""
precompute_macd() {
  MACD_BATCH_RESULT=$(calc_ema_batch "$CACHE_DIR")
}

get_macd_for_code() {
  local code="$1" field="$2"
  # field: ema12|ema26|dif|prev_dif
  echo "$MACD_BATCH_RESULT" | grep "^${code}|" | cut -d'|' -f"$3"
}

# 兼容旧接口：单标的 calc_ema（通过批量结果查找）
calc_ema() {
  local code="$1" n="$2"
  local field_idx=1
  [ "$n" = "12" ] && field_idx=1
  [ "$n" = "26" ] && field_idx=2
  echo "$MACD_BATCH_RESULT" | grep "^${code}|" | cut -d'|' -f$field_idx
}

# MACD DIF
calc_dif() {
  local code="$1"
  echo "$MACD_BATCH_RESULT" | grep "^${code}|" | cut -d'|' -f3
}

# 前一天DIF
calc_prev_dif() {
  local code="$1"
  echo "$MACD_BATCH_RESULT" | grep "^${code}|" | cut -d'|' -f4
}

# =============== v6.0 State机：市场状态判定 ===============
# 替代 quality_score 的涨幅/位置/趋势三项
# 5态输出：STRONG_UP | WEAK_UP | CHOP | WEAK_DOWN | STRONG_DOWN
# ============================================================

compute_market_state() {
  local price="$1" ma5="$2" ma10="$3" ma20="$4" high20="$5" low20="$6" dif="$7" cache="$8"
  
  # 前导零修复
  price=$(echo "$price" | sed 's/^\./0./')
  ma5=$(echo "$ma5" | sed 's/^\./0./')
  ma10=$(echo "$ma10" | sed 's/^\./0./')
  ma20=$(echo "$ma20" | sed 's/^\./0./')
  high20=$(echo "$high20" | sed 's/^\./0./')
  low20=$(echo "$low20" | sed 's/^\./0./')
  dif=$(echo "$dif" | sed 's/^\./0./;s/^-\./-0./')
  
  local state="CHOP" state_score=0
  local is_bullish_arr=0 is_bearish_arr=0
  
  # 均线排列判定
  if [ -n "$ma5" ] && [ -n "$ma10" ] && [ -n "$ma20" ]; then
    if cmp "$ma5 > $ma10 && $ma10 > $ma20"; then
      is_bullish_arr=1
    elif cmp "$ma5 < $ma10 && $ma10 < $ma20"; then
      is_bearish_arr=1
    fi
  fi
  
  # 20日振幅（用于CHOP判定）
  local range_20=0
  if [ -n "$high20" ] && [ -n "$low20" ] && cmp "$low20 > 0"; then
    range_20=$(calc_scale "($high20 - $low20) / $low20 * 100" 2) 2>/dev/null
  fi
  
  # ── STRONG_UP: 多头排列 + 三选一守卫 + MACD>0 ──
  # v6.1: 用辉哥的三选一策略替代旧HH20*0.98单条件
  #   cond1: 价格紧贴前高（排除当天，0.995阈值）— 突破位守护
  #   cond2: 收盘沿5日线强势上行，均线张开>2% — 趋势中继
  #   cond3: 收盘在MA20上方 + 近5日涨幅>5% — 加速段
  # 三选一满足即进STRONG_UP，避免冲高回落/高位回踩被误杀
  if [ "$is_bullish_arr" -eq 1 ] && [ -n "$high20" ] && cmp "$dif > 0"; then
    local cond1=0 cond2=0 cond3=0
    cmp "$price >= $high20 * 0.995" && cond1=1
    cmp "$price > $ma5 && $ma5 > $ma10 * 1.02" && cond2=1
    # cond3: 近5日涨幅 > 5%（从ma5推算：price/ma5-1>5% 即 price>ma5*1.05）
    cmp "$price > $ma20 && $price > $ma5 * 1.05" && cond3=1
    if [ "$cond1" -eq 1 ] || [ "$cond2" -eq 1 ] || [ "$cond3" -eq 1 ]; then
      state="STRONG_UP"; state_score=5
    fi
  # ── WEAK_UP: 多头排列但不满足STRONG_UP ──
  elif [ "$is_bullish_arr" -eq 1 ]; then
    state="WEAK_UP"; state_score=4
  # ── STRONG_DOWN: 空头排列 + 价在20日低点2%内 + MACD<0 ──
  elif [ "$is_bearish_arr" -eq 1 ] && [ -n "$low20" ] && cmp "$price <= $low20 * 1.02" && cmp "$dif < 0"; then
    state="STRONG_DOWN"; state_score=1
  # ── WEAK_DOWN: 空头排列但不满足STRONG_DOWN ──
  elif [ "$is_bearish_arr" -eq 1 ]; then
    state="WEAK_DOWN"; state_score=2
  # ── CHOP: 非多头非空头，或振幅<15% ──
  else
    state="CHOP"; state_score=3
    # 微调：价在MA20上方偏强，下方偏弱
    if [ -n "$ma20" ] && cmp "$price > $ma20 * 1.03"; then
      state="CHOP_UP"; state_score=3.5
    elif [ -n "$ma20" ] && cmp "$price < $ma20 * 0.97"; then
      state="CHOP_DOWN"; state_score=2.5
    fi
  fi
  
  echo "$state|$state_score|$is_bullish_arr|$is_bearish_arr|$range_20"
}

# =============== v6.0 量能因子（独立保留） ===============
# 从 quality_score 中独立出来，作为裁决的辅助确认因子
# ========================================================

compute_volume_factor() {
  local vol="$1" avg10v="$2"
  [ -z "$avg10v" ] || cmp "$avg10v <= 0" && { echo "0|量能:无数据"; return; }
  
  local ratio=$(calc_scale "$vol / $avg10v" 2) 2>/dev/null
  ratio=$(echo "$ratio" | sed 's/^\./0./')
  
  if cmp "$ratio >= 2.0"; then
    echo "1.0|量能:1.0(放量${ratio}x)"
  elif cmp "$ratio >= 1.3"; then
    echo "0.8|量能:0.8(温和放量${ratio}x)"
  elif cmp "$ratio >= 0.8"; then
    echo "0.5|量能:0.5(正常${ratio}x)"
  elif cmp "$ratio >= 0.4"; then
    echo "0.3|量能:0.3(缩量${ratio}x)"
  else
    echo "0.1|量能:0.1(地量${ratio}x)"
  fi
}

# =============== 旧 quality_score 保留（兼容性，逐步废弃） ===============

compute_signal_quality() {
  local change="$1" ratio="$2" price="$3" ma20="$4" high20="$5" low20="$6" cache="$7"
  # 前导零修复（bc -l 输出的 .5 → 0.5）
  ratio=$(echo "$ratio" | sed 's/^\./0./')
  price=$(echo "$price" | sed 's/^\./0./')
  ma20=$(echo "$ma20" | sed 's/^\./0./')
  high20=$(echo "$high20" | sed 's/^\./0./')
  low20=$(echo "$low20" | sed 's/^\./0./')
  local score=0.0 details=""

  # 1️⃣ 涨幅因子（0-1分）: 线性函数 max(0, 1-|chg|/5)，越接近0越好
  local abs_chg=$(echo "$change" | sed 's/^-//' | tr -d '%')
  if [ -z "$abs_chg" ] || [ "$abs_chg" = "0" ]; then
    score=$(calc "$score + 1.0")
    details="${details}涨幅:1.0(无数据)"
  else
    local chg_score=$(calc_scale "if(1 - $abs_chg/5 > 0) 1 - $abs_chg/5 else 0" 2) 2>/dev/null
    [ -z "$chg_score" ] && chg_score=0
    score=$(calc "$score + $chg_score")
    details="${details}涨幅:${chg_score}"
  fi

  # 2️⃣ 量能因子（0-1分）: 成交量相比10日均量的倍数
  if [ -n "$ratio" ] && [ "$ratio" != "0" ] && [ -n "$(echo "$ratio" | grep -E '^[0-9.]')" ]; then
    if cmp "$ratio >= 1.5"; then
      score=$(calc "$score + 1.0")
      details="${details}|量能:1.0"
    elif cmp "$ratio >= 1.3"; then
      score=$(calc "$score + 0.5")
      details="${details}|量能:0.5"
    else
      details="${details}|量能:0"
    fi
  else
    details="${details}|量能:0(无数据)"
  fi

  # 3️⃣ 位置因子（0-1分）: 股价处于MA20附近或突破20日高点
  local pos_score=0
  if [ -n "$ma20" ] && [ -n "$price" ] && cmp "$ma20 > 0"; then
    local dist_to_ma20=$(calc "(($price - $ma20) / $ma20) * 100" 2>/dev/null | sed 's/^-//')
    # 在MA20附近5%以内 -> 支撑位确认
    if [ -n "$dist_to_ma20" ] && cmp "$dist_to_ma20 <= 5"; then
      pos_score=0.5
    fi
    # 突破20日高点 -> 强势启动
    if [ -n "$high20" ] && cmp "$price >= $high20"; then
      pos_score=$(calc "$pos_score + 0.5")
      cmp "$pos_score > 1.0" && pos_score=1.0
    fi
    # 回踩20日低点附近（±3%）-> 关键支撑确认（辉哥指定：回踩支撑位加分）
    if [ -n "$low20" ] && cmp "$low20 > 0"; then
      local signed_dist_low=$(calc_scale "($price - $low20) / $low20 * 100" 2) 2>/dev/null
      local dist_low=$(echo "$signed_dist_low" | sed 's/^-//')
      if cmp "$dist_low <= 3"; then
        # 取max：支撑分不覆盖其他加分，但在其他分数更低时生效
        cmp "$pos_score < 0.3" && pos_score=0.3
      fi
    fi
    # 远离MA20超过10% -> 追高风险（仅上穿方向惩罚，下穿是超跌不罚）
    if [ -n "$dist_to_ma20" ] && cmp "$dist_to_ma20 > 10"; then
      local signed_pos=$(calc_scale "($price - $ma20) / $ma20 * 100" 2) 2>/dev/null
      cmp "$signed_pos > 10" && pos_score=$(calc "$pos_score * 0.5") 2>/dev/null
    fi
  fi
  score=$(calc "$score + $pos_score")
  details="${details}|位置:$pos_score"

  # 4️⃣ 趋势因子（0-1分）: MA20方向 + 高位约束
  local trend_score=0
  # 高位降级：股价偏离MA20超过10%时，趋势因子降级
  local dist_pct=""
  if [ -n "$ma20" ] && [ -n "$price" ] && cmp "$ma20 > 0"; then
    dist_pct=$(calc_scale "($price - $ma20) / $ma20 * 100" 2) 2>/dev/null
  fi

  if [ -n "$ma20" ] && [ -f "$cache" ]; then
    local total_lines=$(wc -l < "$cache" 2>/dev/null)
    if [ "$total_lines" -ge 25 ]; then
      # 用5天前的收盘价估算5日前MA20（尾-5行到尾-24行）
      local prev_ma20=$(tail -25 "$cache" | head -20 | awk '{s+=$1} END{printf "%.2f", s/20}' 2>/dev/null)
      if [ -n "$prev_ma20" ] && cmp "$prev_ma20 > 0"; then
        local trend_slope=$(calc_scale "($ma20 - $prev_ma20) / $prev_ma20 * 100" 2) 2>/dev/null
        # 基础趋势判定
        if cmp "$trend_slope > 1.0"; then
          trend_score=1.0
        elif cmp "$trend_slope < -1.0"; then
          trend_score=0.0
        else
          trend_score=0.5  # 走平
        fi
        # 高位约束：偏离MA20超过10%时趋势分折半（仅上穿，远离均线=追高风险）
        if [ -n "$dist_pct" ] && cmp "$dist_pct > 10"; then
          trend_score=$(calc "$trend_score * 0.5") 2>/dev/null
        fi
        # 短期转弱：股价跌破MA5时趋势分折半
        if [ -n "$ma5" ] && cmp "$ma5 > 0" && cmp "$price < $ma5"; then
          trend_score=$(calc "$trend_score * 0.5") 2>/dev/null
        fi
      fi
    elif cmp "$price > $ma20"; then
      # 缓存不足时降级判断：price>MA20视为弱势向上
      trend_score=0.5
    fi
  fi
  score=$(calc "$score + $trend_score")
  details="${details}|趋势:$trend_score"

  # 5️⃣ 盘前/盘后缓冲因子（0-1分）：量能不可用时，用日线趋势替代
  local buffer_score=0
  if echo "$details" | grep -q '量能:0'; then
    # A: 均线多头排列 MA5>MA10>MA20
    if [ -n "$ma5" ] && [ -n "$ma10" ] && [ -n "$ma20" ]; then
      if cmp "$ma5 > $ma10" && cmp "$ma10 > $ma20"; then
        buffer_score=$(calc "$buffer_score + 0.5")
      fi
    fi
    # B: 股价维持MA5之上（短线未破位）
    if [ -n "$ma5" ] && [ -n "$price" ] && cmp "$price > $ma5"; then
      buffer_score=$(calc "$buffer_score + 0.25")
    fi
    # C: MACD零轴上方
    if [ -n "$dif" ] && cmp "$dif > 0"; then
      buffer_score=$(calc "$buffer_score + 0.25")
    fi
    if cmp "$buffer_score > 0"; then
      details="${details}|缓:$buffer_score"
      score=$(calc "$score + $buffer_score")
    fi
  fi

  # 前导零修复
  score=$(echo "$score" | sed 's/^\./0./;s/^-\./-0./')
  echo "$score|$details"
}

# --------------- 信号级别 ---------------

classify_level() {
  local abs=$(echo "$1" | sed 's/^-//' | tr -d '%')
  cmp "$abs > 7" && { echo "L3_URGENT"; return; }
  cmp "$abs > 4" && { echo "L2_STRONG"; return; }
  cmp "$abs > 2" && { echo "L1_NORMAL"; return; }
}

# =============== v6.0 裁决树：State × Tier 加权裁决 ===============
# 输入: market_state, buy_vote, sell_vote, buy_count, sell_count, volume_factor
# 输出: verdict + strength
# ====================================================================

calc_resonance_v6() {
  local market_state="$1" buy_vote="$2" sell_vote="$3" buy_count="$4" sell_count="$5"
  local volume_factor="$6" tier_summary="$7"
  
  # 前导零修复
  buy_vote=$(echo "$buy_vote" | sed 's/^\./0./')
  sell_vote=$(echo "$sell_vote" | sed 's/^\./0./')
  volume_factor=$(echo "$volume_factor" | sed 's/^\./0./')
  
  local verdict="观望" strength=0
  
  # ── Tier-S 否决权：突破/跌破关键位置一票否决 ──
  # 由 scan_morphology_signals_v6 的 tier_summary 传入
  # 这里通过 buy_vote/sell_vote 已体现（S级权重高）
  
  # ── 加权裁决 ──
  local net_vote=$(calc "$buy_vote - $sell_vote") 2>/dev/null
  
  # 卖出优先：sell_vote >= 0.60 → 卖出确认
  if cmp "$sell_vote >= 0.80"; then
    verdict="卖出确认-减仓"; strength=-3
  elif cmp "$sell_vote >= 0.60"; then
    verdict="卖出预警-关注"; strength=-2
  elif cmp "$sell_vote >= 0.35" && cmp "$buy_vote < 0.30"; then
    verdict="卖出预警-关注"; strength=-1
  fi
  
  # 买入判定（仅在卖出未触发时）
  if [ "$strength" -eq 0 ]; then
    if cmp "$buy_vote >= 0.80 && $sell_vote < 0.30"; then
      verdict="三重共振-出手"; strength=3
    elif cmp "$buy_vote >= 0.55 && $sell_vote < 0.35"; then
      verdict="双重确认-可参与"; strength=2
    elif cmp "$buy_vote >= 0.30"; then
      verdict="单一信号-观察"; strength=1
    fi
  fi
  
  # ── 多风险信号叠加否决：≥3个卖出信号 → 强制降级 ──
  if [ "$strength" -ge 1 ] && [ "$sell_count" -ge 3 ]; then
    if [ "$strength" -eq 3 ]; then
      verdict="双重确认-可参与(多风险信号)"; strength=2
    elif [ "$strength" -eq 2 ]; then
      verdict="单一信号-观察(多风险信号)"; strength=1
    elif [ "$strength" -eq 1 ]; then
      verdict="观望(多风险信号叠加)"; strength=0
    fi
  fi
  
  # ── 量能辅助确认：放量突破加分，缩量反弹降级 ──
  if [ "$strength" -ge 2 ] && cmp "$volume_factor >= 0.8"; then
    :  # 放量确认，维持原级
  elif [ "$strength" -ge 2 ] && cmp "$volume_factor < 0.3"; then
    # 缩量反弹，降一级
    if [ "$strength" -eq 3 ]; then
      verdict="双重确认-可参与(量能不足)"; strength=2
    elif [ "$strength" -eq 2 ]; then
      verdict="单一信号-观察(量能不足)"; strength=1
    fi
  fi
  
  # ── State 上下文裁决 ──
  # STRONG_UP中卖出信号降权（牛市不言顶）
  if [ "$market_state" = "STRONG_UP" ] && [ "$strength" -lt 0 ]; then
    strength=$((strength + 1))  # 卖出一级降为预警
    [ "$strength" -ge 0 ] && { verdict="高位震荡-观望"; strength=0; }
  fi
  # STRONG_DOWN中买入信号降权（熊市不抄底）
  if [ "$market_state" = "STRONG_DOWN" ] && [ "$strength" -gt 0 ]; then
    strength=$((strength - 1))  # 买入降一级
    [ "$strength" -le 0 ] && { verdict="弱势反弹-观望"; strength=0; }
  fi
  
  echo "{\"verdict\":\"$verdict\",\"buy_vote\":$buy_vote,\"sell_vote\":$sell_vote,\"buy_count\":$buy_count,\"sell_count\":$sell_count,\"strength\":$strength,\"volume_factor\":$volume_factor,\"market_state\":\"$market_state\",\"tier_summary\":\"$tier_summary\"}"
}

# =============== v6.0 形态信号 Tier 分级 + only_valid_in 过滤 ===============
# Tier-S: 结构级（否决权）  Tier-A: 强信号(+0.40~+0.60)
# Tier-B: 中信号(+0.15~+0.35)  Tier-C: 弱信号(+0.05~+0.10)
# 每条规则标注 only_valid_in，无效场景权重归零
# ============================================================================

# 获取规则的 Tier 和 only_valid_in
# 返回: "Tier|weight|valid_states"
_get_signal_tier() {
  local rule="$1"
  case "$rule" in
    # ── Tier-S: 结构级（否决权）──
    breakout_up)           echo "S|0.55|ALL" ;;
    breakdown)             echo "S|-0.55|ALL" ;;
    
    # ── Tier-A: 强信号 ──
    morning_star)          echo "A|0.60|CHOP,CHOP_DOWN,WEAK_DOWN,STRONG_DOWN" ;;
    doji_bullish_confirmed) echo "A|0.55|CHOP,CHOP_DOWN,WEAK_DOWN" ;;
    bullish_arrangement)   echo "A|0.50|STRONG_UP,WEAK_UP,CHOP_UP" ;;
    2b_fake_breakdown)     echo "A|0.50|CHOP,WEAK_DOWN,STRONG_DOWN" ;;
    shrink_then_breakout)  echo "A|0.50|CHOP,WEAK_UP,STRONG_UP" ;;
    volume_pullback_support) echo "A|0.50|STRONG_UP,WEAK_UP" ;;
    shrink_reversal)       echo "A|0.50|CHOP,WEAK_DOWN,STRONG_DOWN" ;;
    chip_peak_low_single)  echo "A|0.50|CHOP,CHOP_DOWN,WEAK_DOWN,STRONG_DOWN" ;;
    fairy_guide_confirmed) echo "A|0.45|STRONG_UP,WEAK_UP" ;;
    macd_bottom_div)       echo "A|0.40|WEAK_DOWN,STRONG_DOWN" ;;
    should_fall_strong)    echo "A|0.40|ALL" ;;
    
    # ── Tier-A: 强卖出信号 ──
    shooting_star)         echo "A|-0.50|STRONG_UP,WEAK_UP,CHOP_UP" ;;
    bearish_arrangement)   echo "A|-0.50|STRONG_DOWN,WEAK_DOWN,CHOP_DOWN" ;;
    2b_fake_breakout)      echo "A|-0.50|STRONG_UP,WEAK_UP" ;;
    chip_peak_upper_single) echo "A|-0.45|STRONG_UP,WEAK_UP,CHOP_UP" ;;
    macd_top_div)          echo "A|-0.40|STRONG_UP,WEAK_UP" ;;
    should_rise_fail)      echo "A|-0.40|ALL" ;;
    chip_resistance)       echo "A|-0.40|WEAK_UP,CHOP_UP" ;;
    surge_shooting_star_confirm) echo "A|-0.80|STRONG_UP,WEAK_UP" ;;
    surge_touch_plate_dump) echo "A|-0.60|STRONG_UP,WEAK_UP" ;;
    
    # ── Tier-B: 中信号 ──
    macd_golden_cross)     echo "B|0.35|CHOP,WEAK_UP,STRONG_UP" ;;
    red_three)             echo "B|0.30|STRONG_UP,WEAK_UP,CHOP_UP" ;;
    ma_golden_cross)       echo "B|0.30|CHOP,WEAK_UP,STRONG_UP" ;;
    chip_density_low)      echo "B|0.30|CHOP,CHOP_DOWN,WEAK_DOWN" ;;
    historical_breakthrough) echo "B|0.30|ALL" ;;
    doji_bullish_candidate) echo "B|0.25|CHOP,CHOP_DOWN,WEAK_DOWN" ;;
    vol_up_with_price)     echo "B|0.25|ALL" ;;
    fairy_guide_forming)   echo "B|0.25|STRONG_UP,WEAK_UP" ;;
    macd_death_converging) echo "B|0.25|CHOP,WEAK_DOWN,STRONG_DOWN" ;;
    hammer)                echo "B|0.20|CHOP,WEAK_DOWN,STRONG_DOWN" ;;
    gap_up)                echo "B|0.20|ALL" ;;
    vol_down_shrink)       echo "B|0.20|ALL" ;;
    outperform_sector)     echo "B|0.20|ALL" ;;
    chip_below_cost)       echo "B|0.20|CHOP_DOWN,WEAK_DOWN,STRONG_DOWN" ;;
    
    # ── Tier-B: 中卖出信号 ──
    macd_death_cross)      echo "B|-0.35|STRONG_UP,WEAK_UP,CHOP_UP" ;;
    three_crows)           echo "B|-0.30|STRONG_DOWN,WEAK_DOWN,CHOP_DOWN" ;;
    breakdown)             echo "B|-0.30|ALL" ;;
    ma_death_cross)        echo "B|-0.30|STRONG_DOWN,WEAK_DOWN,CHOP_DOWN" ;;
    vol_down_with_vol)     echo "B|-0.30|ALL" ;;
    limit_down)            echo "B|-0.30|ALL" ;;
    hanging_man)           echo "B|-0.25|STRONG_UP,WEAK_UP" ;;
    # v6.7: gap_down/rsi_overbought 降权（大涨标的跳空+超买是正常特征）
    gap_down)              echo "B|-0.05|ALL" ;;
    rsi_overbought)        echo "B|-0.05|STRONG_UP,WEAK_UP" ;;
    doji_bearish_warn)     echo "B|-0.35|STRONG_UP,WEAK_UP,CHOP_UP" ;;
    macd_death_ongoing)    echo "B|-0.20|STRONG_DOWN,WEAK_DOWN" ;;
    upper_wick)            echo "B|-0.20|STRONG_UP,WEAK_UP" ;;
    approach_resistance)   echo "B|-0.20|WEAK_UP,CHOP_UP" ;;
    
    # ── Tier-C: 弱信号 ──
    ma_convergence_up)     echo "C|0.15|CHOP" ;;
    macd_golden_cross_weak) echo "C|0.15|CHOP,WEAK_DOWN" ;;
    macd_above_zero)       echo "C|0.15|ALL" ;;
    rsi_oversold)          echo "C|0.15|WEAK_DOWN,STRONG_DOWN" ;;
    chip_profit_low)       echo "C|0.15|CHOP_DOWN,WEAK_DOWN,STRONG_DOWN" ;;
    volume_shrink)         echo "C|0.10|ALL" ;;
    
    # ── Tier-C: 弱卖出信号 ──
    surge_extreme_gamble)  echo "C|-0.40|STRONG_UP,WEAK_UP" ;;
    macd_below_zero)       echo "C|-0.15|ALL" ;;
    vol_up_no_vol)         echo "C|-0.15|ALL" ;;
    chip_deviation_high)   echo "C|-0.05|STRONG_UP,WEAK_UP" ;;
    underperform_sector)   echo "C|-0.15|ALL" ;;
    volume_surge)          echo "C|-0.10|ALL" ;;
    
    # ── Tier-C: 偏离度/换手率风险信号（v6.6: 降低权重，大涨标的必然特征）──
    ma5_gap)               echo "C|-0.05|STRONG_UP,WEAK_UP,CHOP_UP" ;;
    turnover_abnormal)     echo "C|-0.05|STRONG_UP,WEAK_UP,CHOP_UP" ;;
    
    # ── Tier-C: 估值预警 ──
    # v6.7: pe_overvalued 降权（辉哥诊断：大涨标的PE高是正常特征，非形态破坏）
    pe_overvalued)         echo "C|-0.05|STRONG_UP,WEAK_UP,CHOP_UP" ;;
    pe_extreme)            echo "C|-0.10|STRONG_UP,WEAK_UP,CHOP_UP" ;;
    
    # ── Tier-C: 获利盘高位风险（v6.6: 从-0.15降至-0.05）──
    chip_profit_high)      echo "C|-0.05|STRONG_UP,WEAK_UP,CHOP_UP" ;;
    
    # ── 中性（0分，不参与投票）──
    doji)                  echo "Z|0|ALL" ;;
    chip_dual_peak)        echo "Z|0|ALL" ;;
    limit_up)              echo "Z|0|ALL" ;;
    ma_convergence_down)   echo "Z|0|ALL" ;;
    *)                     echo "Z|0|ALL" ;;
  esac
}

# 检查规则在当前State下是否有效
_is_valid_in_state() {
  local valid_states="$1" current_state="$2"
  [ "$valid_states" = "ALL" ] && return 0
  # 子状态匹配：CHOP_UP/CHOP_DOWN 匹配 CHOP
  local base_state="${current_state%_*}"
  if echo ",$valid_states," | grep -q ",$current_state,"; then return 0; fi
  if [ "$base_state" != "$current_state" ] && echo ",$valid_states," | grep -q ",$base_state,"; then return 0; fi
  return 1
}

# v6.0 形态评分：Tier分级 + only_valid_in过滤 + 加权投票
# 输入: market_state signals[]
# 输出: "morph_score|buy_vote|sell_vote|buy_count|sell_count|tier_summary"
scan_morphology_signals_v6() {
  local market_state="$1"; shift
  local signals=("$@")
  local has_breakout=0 has_pullback_signal=0
  
  local buy_vote=0 sell_vote=0
  local buy_count=0 sell_count=0
  local tier_a_buy=0 tier_b_buy=0 tier_c_buy=0
  local tier_a_sell=0 tier_b_sell=0 tier_c_sell=0
  local rules_fired=""
  
  for sig in "${signals[@]}"; do
    local rule=$(echo "$sig" | grep -o '"rule":"[^"]*"' | cut -d'"' -f4)
    [ -z "$rule" ] && continue
    
    local tier_info=$(_get_signal_tier "$rule")
    local tier=$(echo "$tier_info" | cut -d'|' -f1)
    local weight=$(echo "$tier_info" | cut -d'|' -f2)
    local valid_states=$(echo "$tier_info" | cut -d'|' -f3)
    
    # only_valid_in 过滤
    if ! _is_valid_in_state "$valid_states" "$market_state"; then
      continue  # 无效场景，静音
    fi
    
    rules_fired="${rules_fired},${rule}"
    
    # 标记突破信号（用于post_breakout_pullback判定）
    case "$rule" in
      breakout_up|historical_breakthrough|morning_star|fairy_guide|shrink_then_breakout)
        has_breakout=1 ;;
      doji|shooting_star|hammer)
        has_pullback_signal=1 ;;
    esac
    
    if cmp "$weight > 0"; then
      buy_vote=$(calc "$buy_vote + $weight")
      ((buy_count++))
      case "$tier" in
        A) ((tier_a_buy++)) ;;
        B) ((tier_b_buy++)) ;;
        C) ((tier_c_buy++)) ;;
      esac
    elif cmp "$weight < 0"; then
      local abs_w=$(echo "$weight" | sed 's/^-//')
      sell_vote=$(calc "$sell_vote + $abs_w")
      ((sell_count++))
      case "$tier" in
        A) ((tier_a_sell++)) ;;
        B) ((tier_b_sell++)) ;;
        C) ((tier_c_sell++)) ;;
      esac
    fi
  done
  
  # morph_score = buy_vote - sell_vote（限幅-1.0~+1.0）
  local morph_score=$(calc "$buy_vote - $sell_vote") 2>/dev/null
  morph_score=$(echo "$morph_score" | sed 's/^\./0./;s/^-\./-0./')
  
  # ── v6.1 post_breakout_pullback：刚突破后回踩企稳 = 加分而非减分 ──
  # 条件：近期有突破信号 + 当前有十字星/缩量等回踩特征 + 趋势未破
  # 命中: morph_score += 0.25 (限幅1.0上限)
  if [ "$has_breakout" -eq 1 ]; then
    if [ "$has_pullback_signal" -eq 1 ]; then
      # 检查是否缩量
      local has_shrink=0
      for sig in "${signals[@]}"; do
        local r=$(echo "$sig" | grep -o '"rule":"[^"]*"' | cut -d'"' -f4)
        [ "$r" = "volume_shrink" ] || [ "$r" = "shrink_reversal" ] && { has_shrink=1; break; }
      done
      if [ "$has_shrink" -eq 1 ] || [ "$market_state" = "STRONG_UP" ] || [ "$market_state" = "WEAK_UP" ]; then
        local bonus=0.25
        local pulled_up=$(calc "$morph_score + $bonus") 2>/dev/null
        cmp "$pulled_up > 1.0" && pulled_up=1.0
        morph_score=$pulled_up
      fi
    fi
  fi
  
  # 前导零修复
  buy_vote=$(echo "$buy_vote" | sed 's/^\./0./')
  sell_vote=$(echo "$sell_vote" | sed 's/^\./0./')
  
  local tier_summary="A买${tier_a_buy}/B买${tier_b_buy}/C买${tier_c_buy}·A卖${tier_a_sell}/B卖${tier_b_sell}/C卖${tier_c_sell}"
  
  echo "$morph_score|$buy_vote|$sell_vote|$buy_count|$sell_count|$tier_summary"
}

# --------------- 主评估流程 ---------------

evaluate() {
  local code="$1" name="$2" price="$3" change="$4" open="$5" high="$6" low="$7" yclose="$8" vol="$9" pe_ttm="${10}"
  local cache="$CACHE_DIR/${code}.day"
  
  # 从缓存预计算所有衍生指标
  local ma5=$(ma_n "$cache" 5)
  local ma10=$(ma_n "$cache" 10)
  local ma20=$(ma_n "$cache" 20)
  local ma60=$(ma_n "$cache" 60)
  local avg10v=$(avgvol_n "$cache" 10)
  local high20=$(high_n "$cache" 20)
  local high20_excl_today=$(high_n_excl_today "$cache" 20)
  local low20=$(low_n "$cache" 20)
  local dif=$(calc_dif "$code")
  local prev_dif=$(calc_prev_dif "$code")
  local prev_close=$(tail -2 "$cache" 2>/dev/null | head -1 | awk '{print $1}')
  
  local level=$(classify_level "$change")
  [ -z "$level" ] && level="L0_NORMAL"
  
  local signals=()
  
  # 调用所有规则（纯计算，零网络）
  for rule_func in $(declare -F | awk '{print $3}' | grep '^rule_'); do
    local result=$($rule_func "$code" "$name" "$price" "$change" \
      "$open" "$high" "$low" "$yclose" "$vol" \
      "$ma5" "$ma10" "$ma20" "$ma60" \
      "$avg10v" "$high20" "$low20" \
      "$dif" "$prev_dif" "$prev_close" \
      "$MARKET_SH" "$MARKET_CY" 2>/dev/null)
    # v6.5: 拆分行（chip_distribution 等多行输出），每行作为一个独立信号
    if [ -n "$result" ]; then
      while IFS= read -r line; do
        [ -n "$line" ] && signals+=("$line")
      done <<< "$result"
    fi
  done
  
  # ── PE 估值判定（内联，避免子 shell 中 $RAW 不可见）──
  [ -n "$pe_ttm" ] && [ "$pe_ttm" != "0" ] && {
    if cmp "$pe_ttm > 300"; then
      signals+=('{"rule":"pe_extreme","direction":"sell","note":"PE_TTM='$pe_ttm'极高估值,风险极大","strength":"high"}')
    elif cmp "$pe_ttm > 120"; then
      signals+=('{"rule":"pe_overvalued","direction":"sell","note":"PE_TTM='$pe_ttm'偏高估值","strength":"medium"}')
    elif cmp "$pe_ttm > 80"; then
      signals+=('{"rule":"pe_overvalued","direction":"sell","note":"PE_TTM='$pe_ttm'估值偏高","strength":"low"}')
    fi
  }
  
  # 指数代码始终输出（供smart_monitor市场过滤器使用）
  if [ ${#signals[@]} -eq 0 ]; then
    [[ $code == "000001" || $code == "399001" || $code == "399006" ]] || return
  fi
  
  # ── v6.0 State机：判定市场状态 ──
  local state_result=$(compute_market_state "$price" "$ma5" "$ma10" "$ma20" "$high20_excl_today" "$low20" "$dif" "$cache")
  local market_state=$(echo "$state_result" | cut -d'|' -f1)
  local state_score=$(echo "$state_result" | cut -d'|' -f2)
  
  # ── v6.0 量能因子（独立）──
  local vol_result=$(compute_volume_factor "$vol" "$avg10v")
  local volume_factor=$(echo "$vol_result" | cut -d'|' -f1)
  local vol_detail=$(echo "$vol_result" | cut -d'|' -f2)
  
  # ── v6.0 Tier分级 + only_valid_in过滤 + 加权投票 ──
  local morph_result=$(scan_morphology_signals_v6 "$market_state" "${signals[@]}")
  local morph_score=$(echo "$morph_result" | cut -d'|' -f1)
  local buy_vote=$(echo "$morph_result" | cut -d'|' -f2)
  local sell_vote=$(echo "$morph_result" | cut -d'|' -f3)
  local buy_count=$(echo "$morph_result" | cut -d'|' -f4)
  local sell_count=$(echo "$morph_result" | cut -d'|' -f5)
  local tier_summary=$(echo "$morph_result" | cut -d'|' -f6)
  [ -z "$morph_score" ] && morph_score=0
  [ -z "$buy_vote" ] && buy_vote=0
  [ -z "$sell_vote" ] && sell_vote=0
  [ -z "$buy_count" ] && buy_count=0
  [ -z "$sell_count" ] && sell_count=0
  
  # ── v6.0 加权裁决 ──
  local resonance=$(calc_resonance_v6 "$market_state" "$buy_vote" "$sell_vote" "$buy_count" "$sell_count" "$volume_factor" "$tier_summary")
  
  # ── 旧版兼容：保留 quality_score / morph_score / total_score_ext ──
  local quality_score=$state_score
  local total_score_ext=$(calc "$state_score + $morph_score") 2>/dev/null
  total_score_ext=$(echo "$total_score_ext" | sed 's/^\./0./;s/^-\./-0./')
  
  # ═══════════════════════════════════════════════════════════
  # v7.0 IQ_Score：统一投资价值评分（0~100）
  # 公式：State基础分 × 形态乘数 + 风险惩罚
  # 高分=好标的+好时机+值得买，低分=风险大+不值得碰
  # ═══════════════════════════════════════════════════════════
  local iq_score=50 iq_grade="C-观望" iq_detail=""
  
  # ── Step 1: State基础分（0~60）──
  # 将5态映射到连续分值，同态内用均线发散度/价格位置微调
  local state_base=30  # 默认中性
  case "$market_state" in
    STRONG_UP)   state_base=55
      # 同态微调：均线发散度越大越强，但偏离MA20超15%扣分（追高风险）
      if [ -n "$ma20" ] && cmp "$ma20 > 0"; then
        local dev_ma20=$(calc_scale "($price - $ma20) / $ma20 * 100" 1) 2>/dev/null
        dev_ma20=$(echo "$dev_ma20" | sed 's/^-//')
        cmp "$dev_ma20 < 5" && state_base=58   # 紧贴MA20启动，最佳
        cmp "$dev_ma20 > 15" && state_base=50  # 偏离过大，追高风险
      fi
      ;;
    WEAK_UP)     state_base=42
      # 同态微调：价在MA5上方+2，在MA5下方-3
      [ -n "$ma5" ] && cmp "$price > $ma5" && state_base=44
      [ -n "$ma5" ] && cmp "$price < $ma5" && state_base=39
      ;;
    CHOP_UP)     state_base=38 ;;
    CHOP)        state_base=30 ;;
    CHOP_DOWN)   state_base=22 ;;
    WEAK_DOWN)   state_base=18
      # 同态微调：价在MA5上方+2（可能止跌），在MA5下方-2
      [ -n "$ma5" ] && cmp "$price > $ma5" && state_base=20
      ;;
    STRONG_DOWN) state_base=8
      # 同态微调：价在20日低点附近可能超跌反弹+3
      [ -n "$low20" ] && cmp "$price <= $low20 * 1.03" && state_base=11
      ;;
  esac
  
  # ── Step 2: 形态乘数（0.5~1.5）──
  # morph_score ∈ [-1.0, +1.0] → multiplier ∈ [0.5, 1.5]
  # 线性映射：multiplier = 1.0 + morph_score × 0.5
  local morph_mult=$(calc "1.0 + $morph_score * 0.5") 2>/dev/null
  morph_mult=$(echo "$morph_mult" | sed 's/^\./0./;s/^-\./-0./')
  # 限幅
  cmp "$morph_mult < 0.5" && morph_mult=0.5
  cmp "$morph_mult > 1.5" && morph_mult=1.5
  
  # ── Step 3: 量能调节（-8~+8）──
  local vol_adj=0
  case "$volume_factor" in
    1.0) vol_adj=8 ;;   # 放量2x+，强势确认
    0.8) vol_adj=5 ;;   # 温和放量
    0.5) vol_adj=0 ;;   # 正常
    0.3) vol_adj=-3 ;;  # 缩量（上涨中=蓄力+3，下跌中=无力-5）
    0.1) vol_adj=-8 ;;  # 地量
  esac
  # 缩量在上涨趋势中是蓄力而非弱势
  if cmp "$volume_factor == 0.3" && [ "$market_state" = "STRONG_UP" ]; then
    vol_adj=3  # 强势股缩量回踩=洗盘，加分
  fi
  
  # ── Step 4: 风险惩罚（0~-30）──
  local risk_penalty=0
  # 卖出信号数量惩罚
  [ "$sell_count" -ge 1 ] && risk_penalty=$(calc "$risk_penalty + $sell_count * 3")
  [ "$sell_count" -ge 3 ] && risk_penalty=$(calc "$risk_penalty + 5")  # 多信号叠加额外罚
  # 高位风险
  cmp "$profit_pct > 90" && risk_penalty=$(calc "$risk_penalty + 8")
  cmp "$profit_pct > 95" && risk_penalty=$(calc "$risk_penalty + 5")
  # 跌停/涨停一字板
  [ "$today_down_limit" -eq 1 ] && risk_penalty=$(calc "$risk_penalty + 15")
  [ "$today_limit" -eq 1 ] && risk_penalty=$(calc "$risk_penalty + 5")  # 涨停不追
  # 限幅
  cmp "$risk_penalty > 30" && risk_penalty=30
  
  # ── Step 5: 合成 IQ_Score ──
  iq_score=$(calc "$state_base * $morph_mult + $vol_adj - $risk_penalty") 2>/dev/null
  iq_score=$(echo "$iq_score" | sed 's/^\./0./;s/^-\./-0./')
  # 限幅 0~100
  cmp "$iq_score < 0" && iq_score=0
  cmp "$iq_score > 100" && iq_score=100
  iq_score=$(calc_scale "$iq_score" 0) 2>/dev/null
  
  # ── Step 6: 评级映射 ──
  if cmp "$iq_score >= 80"; then
    iq_grade="A-强烈推荐"
  elif cmp "$iq_score >= 70"; then
    iq_grade="B+-推荐买入"
  elif cmp "$iq_score >= 60"; then
    iq_grade="B-可以买入"
  elif cmp "$iq_score >= 50"; then
    iq_grade="C+-谨慎参与"
  elif cmp "$iq_score >= 40"; then
    iq_grade="C-观望"
  elif cmp "$iq_score >= 30"; then
    iq_grade="D-偏弱回避"
  elif cmp "$iq_score >= 20"; then
    iq_grade="E-弱势勿碰"
  else
    iq_grade="F-高风险远离"
  fi
  
  # 构建详情
  iq_detail="state=${state_base}×morph=${morph_mult}+vol=${vol_adj}-risk=${risk_penalty}"
  
  # ── v6.3 entry_score v2：五类买点质量评分 ──
  # 从 signals 中提取关键字段
  local profit_pct=0 dev_cost=0 today_limit=0 today_down_limit=0 vol_ratio=0
  local has_breakout=0 has_bottom_div=0 has_oversold=0 has_hammer=0
  local has_morning_star=0 has_fairy_guide=0 has_should_fall_strong=0
  local has_red_three=0 has_gap_up=0 has_bullish_arr=0 has_macd_above=0
  local has_chip_density_low=0 has_chip_below_cost=0 has_chip_peak_low=0
  local has_ma_convergence=0 has_ma_golden_cross=0 has_macd_golden=0
  local has_shrink=0 has_vol_pullback=0 has_vol_surge=0
  local has_shooting_star=0 has_hanging_man=0 has_breakdown=0 has_2b_fake=0
  local has_bearish_arr=0 has_death_ongoing=0 has_should_rise_fail=0
  local has_limit_down=0 has_upper_wick=0
  
  for sig in "${signals[@]}"; do
    local r=$(echo "$sig" | grep -o '"rule":"[^"]*"' | cut -d'"' -f4)
    local note=$(echo "$sig" | grep -o '"note":"[^"]*"' | cut -d'"' -f4)
    case "$r" in
      chip_profit_high) profit_pct=$(echo "$note" | grep -oP '\d+\.?\d*(?=%\-兑现压力大)' | head -1) ;;
      chip_deviation_high) dev_cost=$(echo "$note" | grep -oP '\d+\.?\d*(?=%\-获利盘丰厚)' | head -1) ;;
      limit_up) today_limit=1 ;;
      limit_down) today_down_limit=1 ;;
      breakout_up|historical_breakthrough|shrink_then_breakout) has_breakout=1 ;;
      macd_bottom_div) has_bottom_div=1 ;;
      rsi_oversold) has_oversold=1 ;;
      hammer) has_hammer=1 ;;
      morning_star) has_morning_star=1 ;;
      fairy_guide) has_fairy_guide=1 ;;
      should_fall_strong) has_should_fall_strong=1 ;;
      red_three) has_red_three=1 ;;
      gap_up) has_gap_up=1 ;;
      bullish_arrangement) has_bullish_arr=1 ;;
      macd_above_zero) has_macd_above=1 ;;
      chip_density_low) has_chip_density_low=1 ;;
      chip_below_cost) has_chip_below_cost=1 ;;
      chip_peak_low_single) has_chip_peak_low=1 ;;
      ma_convergence_up) has_ma_convergence=1 ;;
      ma_golden_cross) has_ma_golden_cross=1 ;;
      macd_golden_cross) has_macd_golden=1 ;;
      volume_shrink) has_shrink=1 ;;
      volume_pullback_support) has_vol_pullback=1 ;;
      volume_surge) has_vol_surge=1 ;;
      shooting_star) has_shooting_star=1 ;;
      hanging_man) has_hanging_man=1 ;;
      breakdown) has_breakdown=1 ;;
      2b_fake_breakout) has_2b_fake=1 ;;
      upper_wick) has_upper_wick=1 ;;
      bearish_arrangement) has_bearish_arr=1 ;;
      macd_death_ongoing) has_death_ongoing=1 ;;
      should_rise_fail) has_should_rise_fail=1 ;;
    esac
  done
  [ -z "$profit_pct" ] && profit_pct=0
  [ -z "$dev_cost" ] && dev_cost=0
  vol_ratio=$VOL_RATIO_GLOBAL
  [ -z "$vol_ratio" ] && vol_ratio=0
  
  local entry_type="NO_ENTRY" entry_trigger="" entry_score=-1
  
  # 计算是否在MA5/MA10附近
  local near_ma5=0 near_ma10=0
  [ "$ma5" != "n/a" ] && [ -n "$ma5" ] && {
    local dist_ma5=$(calc_scale "($price - $ma5) / $price" 4) 2>/dev/null
    dist_ma5=$(echo "$dist_ma5" | sed 's/^-//')
    cmp "$dist_ma5 < 0.015" && near_ma5=1
  }
  [ "$ma10" != "n/a" ] && [ -n "$ma10" ] && {
    local dist_ma10=$(calc_scale "($price - $ma10) / $price" 4) 2>/dev/null
    dist_ma10=$(echo "$dist_ma10" | sed 's/^-//')
    cmp "$dist_ma10 < 0.02" && near_ma10=1
  }
  
  local is_shrink=0
  cmp "$vol_ratio < 0.85" && is_shrink=1
  
  # ── 1. 回踩买 PULLBACK_BUY ──
  if [ "$market_state" = "STRONG_UP" ] && [ "$today_limit" -eq 0 ] && [ "$today_down_limit" -eq 0 ]; then
    if [ "$near_ma5" -eq 1 ] || [ "$near_ma10" -eq 1 ]; then
      if [ "$is_shrink" -eq 1 ] || cmp "$profit_pct < 90"; then
        entry_type="PULLBACK_BUY"
        entry_score=0.30
        cmp "$profit_pct > 95" && entry_score=$(calc "$entry_score - 0.05")
        cmp "$dev_cost > 30" && entry_score=$(calc "$entry_score - 0.10")
        [ "$has_vol_pullback" -eq 1 ] && entry_score=$(calc "$entry_score + 0.05")
        [ "$has_bullish_arr" -eq 1 ] && entry_score=$(calc "$entry_score + 0.03")
        [ "$has_macd_above" -eq 1 ] && entry_score=$(calc "$entry_score + 0.02")
        [ "$has_red_three" -eq 1 ] && entry_score=$(calc "$entry_score + 0.02")
        [ "$near_ma5" -eq 1 ] && entry_trigger="MA5≈$ma5 缩量回踩" || entry_trigger="MA10≈$ma10 缩量回踩"
      fi
    fi
  fi
  
  # ── 2. 突破买 BREAKOUT_BUY ──
  if [ "$entry_type" = "NO_ENTRY" ] && [ "$market_state" = "STRONG_UP" ] && [ "$today_limit" -eq 0 ] && [ "$today_down_limit" -eq 0 ]; then
    if [ "$has_breakout" -eq 1 ] && cmp "$profit_pct < 90" && cmp "$dev_cost < 20"; then
      entry_type="BREAKOUT_BUY"
      entry_score=0.25
      [ "$has_vol_surge" -eq 1 ] && entry_score=$(calc "$entry_score + 0.03")
      [ "$has_bullish_arr" -eq 1 ] && entry_score=$(calc "$entry_score + 0.02")
      [ "$has_macd_above" -eq 1 ] && entry_score=$(calc "$entry_score + 0.02")
      entry_trigger="突破确认 获利盘${profit_pct}%"
    fi
  fi
  
  # ── 3. 底部反转买 REVERSAL_BUY ──
  if [ "$entry_type" = "NO_ENTRY" ] && [ "$today_limit" -eq 0 ] && [ "$today_down_limit" -eq 0 ]; then
    local rev_score=0 rev_reasons=""
    [ "$has_bottom_div" -eq 1 ] && { rev_score=$(calc "$rev_score + 0.20"); rev_reasons="${rev_reasons}MACD底背离+"; }
    [ "$has_morning_star" -eq 1 ] && { rev_score=$(calc "$rev_score + 0.20"); rev_reasons="${rev_reasons}早晨之星+"; }
    [ "$has_should_fall_strong" -eq 1 ] && { rev_score=$(calc "$rev_score + 0.15"); rev_reasons="${rev_reasons}该跌不跌+"; }
    [ "$has_fairy_guide" -eq 1 ] && { rev_score=$(calc "$rev_score + 0.15"); rev_reasons="${rev_reasons}仙人指路+"; }
    [ "$has_hammer" -eq 1 ] && [ "$has_chip_below_cost" -eq 1 -o "$has_chip_density_low" -eq 1 -o "$has_chip_peak_low" -eq 1 ] && { rev_score=$(calc "$rev_score + 0.15"); rev_reasons="${rev_reasons}锤子线低位+"; }
    [ "$has_oversold" -eq 1 ] && { rev_score=$(calc "$rev_score + 0.10"); rev_reasons="${rev_reasons}RSI超卖+"; }
    [ "$has_chip_density_low" -eq 1 -o "$has_chip_peak_low" -eq 1 ] && { rev_score=$(calc "$rev_score + 0.08"); rev_reasons="${rev_reasons}低位密集+"; }
    [ "$has_shrink" -eq 1 ] && [ "$has_breakout" -eq 1 ] && { rev_score=$(calc "$rev_score + 0.10"); rev_reasons="${rev_reasons}缩量后突破+"; }
    [ "$has_ma_convergence" -eq 1 ] && { rev_score=$(calc "$rev_score + 0.08"); rev_reasons="${rev_reasons}均线收敛+"; }
    rev_reasons=$(echo "$rev_reasons" | sed 's/+$//; s/+/+/g')
    if cmp "$rev_score >= 0.20"; then
      entry_type="REVERSAL_BUY"
      cmp "$rev_score > 0.40" && rev_score=0.40
      entry_score=$rev_score
      entry_trigger="$rev_reasons"
    fi
  fi
  
  # ── 4. 趋势中继买 TREND_CONTINUE ──
  # 注：在STRONG_UP+获利盘过热时，趋势中继信号被NO_ENTRY覆盖
  if [ "$entry_type" = "NO_ENTRY" ] && [ "$market_state" = "STRONG_UP" ] && [ "$today_limit" -eq 0 ] && [ "$today_down_limit" -eq 0 ]; then
    # 获利盘>=90%时跳过趋势中继（过热不追，等回踩）
    if cmp "$profit_pct < 90"; then
      local cont_score=0 cont_reasons=""
      [ "$has_red_three" -eq 1 ] && { cont_score=$(calc "$cont_score + 0.10"); cont_reasons="${cont_reasons}红三兵+"; }
      [ "$has_gap_up" -eq 1 ] && [ "$has_hanging_man" -eq 0 ] && { cont_score=$(calc "$cont_score + 0.08"); cont_reasons="${cont_reasons}跳空+"; }
      [ "$has_bullish_arr" -eq 1 ] && [ "$has_macd_above" -eq 1 ] && { cont_score=$(calc "$cont_score + 0.10"); cont_reasons="${cont_reasons}多头零轴上+"; }
      [ "$has_ma_golden_cross" -eq 1 -o "$has_macd_golden" -eq 1 ] && { cont_score=$(calc "$cont_score + 0.08"); cont_reasons="${cont_reasons}金叉+"; }
      cont_reasons=$(echo "$cont_reasons" | sed 's/+$//; s/+/+/g')
      if cmp "$cont_score >= 0.15"; then
        entry_type="TREND_CONTINUE"
        cmp "$cont_score > 0.30" && cont_score=0.30
        entry_score=$cont_score
        entry_trigger="$cont_reasons"
      fi
    fi
  fi
  
  # ── 5. 超跌反弹买 OVERSOLD_BUY ──
  if [ "$entry_type" = "NO_ENTRY" ] && [ "$today_limit" -eq 0 ] && [ "$today_down_limit" -eq 0 ]; then
    if [ "$market_state" = "WEAK_DOWN" ] || [ "$market_state" = "CHOP_DOWN" ]; then
      local over_score=0 over_reasons=""
      [ "$has_chip_below_cost" -eq 1 ] && { over_score=$(calc "$over_score + 0.10"); over_reasons="${over_reasons}低于成本+"; }
      [ "$has_chip_density_low" -eq 1 -o "$has_chip_peak_low" -eq 1 ] && { over_score=$(calc "$over_score + 0.08"); over_reasons="${over_reasons}低位密集+"; }
      [ "$has_bottom_div" -eq 1 ] && { over_score=$(calc "$over_score + 0.15"); over_reasons="${over_reasons}MACD底背离+"; }
      [ "$has_oversold" -eq 1 ] && { over_score=$(calc "$over_score + 0.10"); over_reasons="${over_reasons}RSI超卖+"; }
      [ "$has_hammer" -eq 1 ] && { over_score=$(calc "$over_score + 0.08"); over_reasons="${over_reasons}锤子线+"; }
      [ "$has_should_fall_strong" -eq 1 ] && { over_score=$(calc "$over_score + 0.12"); over_reasons="${over_reasons}该跌不跌+"; }
      [ "$has_ma_convergence" -eq 1 ] && { over_score=$(calc "$over_score + 0.08"); over_reasons="${over_reasons}均线收敛+"; }
      over_reasons=$(echo "$over_reasons" | sed 's/+$//; s/+/+/g')
      if cmp "$over_score >= 0.15"; then
        entry_type="OVERSOLD_BUY"
        cmp "$over_score > 0.35" && over_score=0.35
        entry_score=$over_score
        entry_trigger="$over_reasons"
      fi
    fi
  fi
  
  # ── 6. 不买区 NO_ENTRY（含涨停/跌停/过热/弱势）──
  if [ "$entry_type" = "NO_ENTRY" ]; then
    if [ "$today_limit" -eq 1 ]; then
      entry_trigger="今日涨停·不追"
    elif [ "$today_down_limit" -eq 1 ]; then
      entry_trigger="跌停·不碰"
    elif [ "$market_state" = "STRONG_UP" ] && cmp "$profit_pct >= 98 && $dev_cost > 25"; then
      local potential=""
      [ "$ma5" != "n/a" ] && [ -n "$ma5" ] && potential="等回踩MA5≈$ma5"
      entry_trigger="获利盘${profit_pct}%+偏离${dev_cost}%·过热 | ${potential}"
    elif [ "$market_state" = "STRONG_UP" ] && cmp "$profit_pct >= 90"; then
      local potential=""
      [ "$ma5" != "n/a" ] && [ -n "$ma5" ] && potential="等回踩MA5≈$ma5"
      entry_trigger="获利盘${profit_pct}%·偏热 | ${potential}"
    elif [ "$market_state" = "CHOP" ] || [ "$market_state" = "CHOP_UP" ]; then
      entry_trigger="震荡·观望"
    elif [ "$market_state" = "WEAK_DOWN" ] || [ "$market_state" = "CHOP_DOWN" ]; then
      entry_trigger="弱势·观望"
    else
      entry_trigger="无明确买点"
    fi
  fi
  
  # 拼接 signals JSON 数组
  local json_sigs=$(IFS=,; echo "${signals[*]}")
  
  echo "{
  \"code\":\"$code\",
  \"name\":\"$name\",
  \"price\":$price,
  \"change_pct\":\"$change\",
  \"open\":\"$open\",
  \"high\":\"$high\",
  \"low\":\"$low\",
  \"ma5\":\"${ma5:-n/a}\",
  \"ma20\":\"${ma20:-n/a}\",
  \"price_level\":\"$level\",
  \"market_state\":\"$market_state\",
  \"state_score\":$state_score,
  \"volume_factor\":$volume_factor,
  \"morph_score\":$morph_score,
  \"buy_vote\":$buy_vote,
  \"sell_vote\":$sell_vote,
  \"total_score_ext\":$total_score_ext,
  \"iq_score\":$iq_score,
  \"iq_grade\":\"$iq_grade\",
  \"iq_detail\":\"$iq_detail\",
  \"entry_type\":\"$entry_type\",
  \"entry_trigger\":\"$entry_trigger\",
  \"entry_score\":$entry_score,
  \"score_details\":\"state=$market_state|vol=$vol_detail|$tier_summary\",
  \"resonance\":$resonance,
  \"signals\":[$json_sigs]
}"
}

# --------------- 加载规则（不执行网络调用） ---------------

REPORT_FILE="/tmp/stock_signals_$$.json"

for rule in "$RULES_DIR"/*.sh; do
  [ -f "$rule" ] && source "$rule"
done

# 预取大盘状态
MARKET=$(fetch_market)
MARKET_SH=$(echo "$MARKET" | cut -d'|' -f1)
MARKET_CY=$(echo "$MARKET" | cut -d'|' -f2)

# 预取全池行情
# 守卫：source时跳过主逻辑（只加载函数定义）
# 直接执行时（bash engine.sh）才跑主逻辑
if [ "$(basename "$0" 2>/dev/null)" != "engine.sh" ]; then
  return  # source进来，跳过主逻辑
fi

RAW=$(fetch_bulk)

# --------------- MACD批量预计算（v6.4优化：一次python3算所有标的）---------------
precompute_macd
# --------------- MACD预计算结束 ---------------

# --------------- 板块相对强度预解析 ---------------
# 从RAW提取5个核心板块ETF涨跌幅（规则函数通过全局变量访问）
etf_chg() { echo "$RAW" | grep -m1 "$1" | awk -F'~' '{print $33}' | tr -d '%'; }
ETF_CHG_CHIP=$(etf_chg "sh516640"); ETF_CHG_CHIP=${ETF_CHG_CHIP:-0}
ETF_CHG_MACHINE=$(etf_chg "sz159667"); ETF_CHG_MACHINE=${ETF_CHG_MACHINE:-0}
ETF_CHG_INNOVATION=$(etf_chg "sz159858"); ETF_CHG_INNOVATION=${ETF_CHG_INNOVATION:-0}
ETF_CHG_CONSUME=$(etf_chg "sz159928"); ETF_CHG_CONSUME=${ETF_CHG_CONSUME:-0}
ETF_CHG_METAL=$(etf_chg "sh512400"); ETF_CHG_METAL=${ETF_CHG_METAL:-0}
# --------------- 板块预解析结束 ----------------

# --------------- 计算概念板块基准 ---------------
# 使用独立脚本查询概念成分股（独立API调用，确保全量覆盖）
bash "$SIGNAL_DIR/compute_concept_benchmarks.sh" compute >/dev/null 2>&1
# --------------- 概念基准计算结束 ---------------

# 逐标评估
first=true
echo "[" > "$REPORT_FILE"

while read code; do
  [ -z "$code" ] && continue
  pfx="sh"; [[ $code != 6* && $code != "000001" ]] && pfx="sz"
  d=$(echo "$RAW" | grep -m1 "${pfx}${code}")
  [ -z "$d" ] && continue
  
  name=$(echo "$d"    | awk -F'~' '{print $2}')
  price=$(echo "$d"   | awk -F'~' '{print $4}')
  change=$(echo "$d"  | awk -F'~' '{print $33}' | tr -d '%')
  open=$(echo "$d"    | awk -F'~' '{print $6}')
  high=$(echo "$d"    | awk -F'~' '{print $34}')
  low=$(echo "$d"     | awk -F'~' '{print $35}')
  yclose=$(echo "$d"  | awk -F'~' '{print $5}')
  vol=$(echo "$d" | awk -F'~' '{print $36}' | awk -F/ '{print $2}')  # 手数
  # 换手率($39)和量比($50)（全局变量，规则函数可访问）
  # gtimg字段定义（辉哥确认）：下标47=涨停价, 48=跌停价, 49=量比
  # awk从1开始计数，所以$50=下标49=量比
  STOCK_TURNOVER=$(echo "$d" | awk -F'~' '{print $39}' 2>/dev/null)
  VOL_RATIO_GLOBAL=$(echo "$d" | awk -F'~' '{print $50}' 2>/dev/null)
  [ -z "$STOCK_TURNOVER" ] && STOCK_TURNOVER=0
  [ -z "$VOL_RATIO_GLOBAL" ] && VOL_RATIO_GLOBAL=0
  # 内外盘（全局变量，规则函数可访问）
  OUTER_DISK=$(echo "$d" | awk -F'~' '{print $8}' 2>/dev/null)
  INNER_DISK=$(echo "$d" | awk -F'~' '{print $9}' 2>/dev/null)
  [ -z "$OUTER_DISK" ] && OUTER_DISK=0
  [ -z "$INNER_DISK" ] && INNER_DISK=0
  
  # PE_TTM 提取（gtimg 字段40）
  PE_TTM=$(echo "$d" | awk -F'~' '{print $40}' | sed 's/[^0-9.]//g')
  [ -z "$PE_TTM" ] && PE_TTM=0
  
  [ -z "$price" ] || [ "$price" = "0.000" ] && continue
  
  result=$(evaluate "$code" "$name" "$price" "$change" "$open" "$high" "$low" "$yclose" "$vol" "$PE_TTM")
  [ -n "$result" ] && {
    $first || echo "," >> "$REPORT_FILE"
    echo "$result" >> "$REPORT_FILE"
    first=false
  }
done < <(get_all_codes)

echo "]" >> "$REPORT_FILE"
echo "signal_file=$REPORT_FILE"
# v6.2 后处理：修复 signals 缺逗号 + 前导零（先修复再解析）
python3 -c "
import re, json
with open('$REPORT_FILE') as f:
    content = f.read()
# 修复1: signals 数组内 JSON 对象间缺逗号（多行输出导致 }\\n{）
content = re.sub(r'\\}(?:\\s*\\n)+\\s*\\{', '},{', content)
# 修复1b: 捕获 "]\n[" 的情况（最外层数组）
content = re.sub(r"\]\s*\n\s*\[", "],[", content)
# 修复2: :. → :0. （bc输出.5而不是0.5）
content = re.sub(r':\\.(\\d)', r':0.\\1', content)
# 修复3: 对象末尾 ,{ 但缺少闭合 }（chip_distribution 信号拼接异常）
content = re.sub(r':\\"[a-z]+\\"\\s*,\\s*\\{', lambda m: m.group(0).replace(',{', '},{'), content)
with open('$REPORT_FILE', 'w') as f:
    f.write(content)
# 验证 JSON 合法性（失败时只警告，不阻塞）
import sys
with open('$REPORT_FILE') as f:
    try:
        json.load(f)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"⚠️ 后处理JSON验证失败: {e}\n")
" 2>/dev/null

# 后处理：计算概念板块相对强度
python3 "$SIGNAL_DIR/concept_relative_strength.py" 2>/dev/null
# 合并概念信号到引擎输出，生成统一信号文件
python3 "$SIGNAL_DIR/merge_concept_signals.py" "$REPORT_FILE" 2>/dev/null

# 清理旧临时信号文件（保留最近5个）
ls -t /tmp/stock_signals_*.json 2>/dev/null | tail -n +6 | xargs rm -f 2>/dev/null
