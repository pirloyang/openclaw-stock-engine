#!/bin/bash
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
calc_ema() {
  local f="$1" n="$2"
  [ ! -f "$f" ] && return
  local total=$(wc -l < "$f")
  [ "$total" -lt "$n" ] && return
  python3 -c "
f=open('$f'); lines=f.readlines(); f.close()
n=$n
prices=[]
for l in lines:
  parts=l.split()
  if len(parts) >= 1:
    try: prices.append(float(parts[0]))
    except: pass
if len(prices) < n: exit(0)
ema=prices[0]
k=2/(n+1)
for p in prices[1:]:
    ema=p*k+ema*(1-k)
print(f'{ema:.2f}')
" 2>/dev/null
}

# MACD DIF (EMA12 - EMA26) — 全序列递推
calc_dif() {
  local f="$1"
  [ ! -f "$f" ] || [ "$(wc -l < "$f")" -lt 26 ] && return
  local ema12=$(calc_ema "$f" 12)
  local ema26=$(calc_ema "$f" 26)
  [ -z "$ema12" ] || [ -z "$ema26" ] && return
  echo "scale=2; $ema12 - $ema26" | bc -l 2>/dev/null | sed 's/^\./0./;s/^-\./-0./'
}

# v5.3: 前一天DIF — 去掉最后一行后用全序列递推
calc_prev_dif() {
  local f="$1"
  [ ! -f "$f" ] || [ "$(wc -l < "$f")" -lt 27 ] && return
  local tmp=$(mktemp)
  head -n -1 "$f" > "$tmp"
  local result=$(calc_dif "$tmp")
  rm -f "$tmp"
  echo "$result"
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
    if [ "$(echo "$ma5 > $ma10 && $ma10 > $ma20" | bc -l 2>/dev/null)" = "1" ]; then
      is_bullish_arr=1
    elif [ "$(echo "$ma5 < $ma10 && $ma10 < $ma20" | bc -l 2>/dev/null)" = "1" ]; then
      is_bearish_arr=1
    fi
  fi
  
  # 20日振幅（用于CHOP判定）
  local range_20=0
  if [ -n "$high20" ] && [ -n "$low20" ] && [ "$(echo "$low20 > 0" | bc -l 2>/dev/null)" = "1" ]; then
    range_20=$(echo "scale=2; ($high20 - $low20) / $low20 * 100" | bc -l 2>/dev/null)
  fi
  
  # ── STRONG_UP: 多头排列 + 三选一守卫 + MACD>0 ──
  # v6.1: 用辉哥的三选一策略替代旧HH20*0.98单条件
  #   cond1: 价格紧贴前高（排除当天，0.995阈值）— 突破位守护
  #   cond2: 收盘沿5日线强势上行，均线张开>2% — 趋势中继
  #   cond3: 收盘在MA20上方 + 近5日涨幅>5% — 加速段
  # 三选一满足即进STRONG_UP，避免冲高回落/高位回踩被误杀
  if [ "$is_bullish_arr" -eq 1 ] && [ -n "$high20" ] && [ "$(echo "$dif > 0" | bc -l 2>/dev/null)" = "1" ]; then
    local cond1=0 cond2=0 cond3=0
    [ "$(echo "$price >= $high20 * 0.995" | bc -l 2>/dev/null)" = "1" ] && cond1=1
    [ "$(echo "$price > $ma5 && $ma5 > $ma10 * 1.02" | bc -l 2>/dev/null)" = "1" ] && cond2=1
    # cond3: 近5日涨幅 > 5%（从ma5推算：price/ma5-1>5% 即 price>ma5*1.05）
    [ "$(echo "$price > $ma20 && $price > $ma5 * 1.05" | bc -l 2>/dev/null)" = "1" ] && cond3=1
    if [ "$cond1" -eq 1 ] || [ "$cond2" -eq 1 ] || [ "$cond3" -eq 1 ]; then
      state="STRONG_UP"; state_score=5
    fi
  # ── WEAK_UP: 多头排列但不满足STRONG_UP ──
  elif [ "$is_bullish_arr" -eq 1 ]; then
    state="WEAK_UP"; state_score=4
  # ── STRONG_DOWN: 空头排列 + 价在20日低点2%内 + MACD<0 ──
  elif [ "$is_bearish_arr" -eq 1 ] && [ -n "$low20" ] && [ "$(echo "$price <= $low20 * 1.02" | bc -l 2>/dev/null)" = "1" ] && [ "$(echo "$dif < 0" | bc -l 2>/dev/null)" = "1" ]; then
    state="STRONG_DOWN"; state_score=1
  # ── WEAK_DOWN: 空头排列但不满足STRONG_DOWN ──
  elif [ "$is_bearish_arr" -eq 1 ]; then
    state="WEAK_DOWN"; state_score=2
  # ── CHOP: 非多头非空头，或振幅<15% ──
  else
    state="CHOP"; state_score=3
    # 微调：价在MA20上方偏强，下方偏弱
    if [ -n "$ma20" ] && [ "$(echo "$price > $ma20 * 1.03" | bc -l 2>/dev/null)" = "1" ]; then
      state="CHOP_UP"; state_score=3.5
    elif [ -n "$ma20" ] && [ "$(echo "$price < $ma20 * 0.97" | bc -l 2>/dev/null)" = "1" ]; then
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
  [ -z "$avg10v" ] || [ "$(echo "$avg10v <= 0" | bc -l 2>/dev/null)" = "1" ] && { echo "0|量能:无数据"; return; }
  
  local ratio=$(echo "scale=2; $vol / $avg10v" | bc -l 2>/dev/null)
  ratio=$(echo "$ratio" | sed 's/^\./0./')
  
  if [ "$(echo "$ratio >= 2.0" | bc -l 2>/dev/null)" = "1" ]; then
    echo "1.0|量能:1.0(放量${ratio}x)"
  elif [ "$(echo "$ratio >= 1.3" | bc -l 2>/dev/null)" = "1" ]; then
    echo "0.8|量能:0.8(温和放量${ratio}x)"
  elif [ "$(echo "$ratio >= 0.8" | bc -l 2>/dev/null)" = "1" ]; then
    echo "0.5|量能:0.5(正常${ratio}x)"
  elif [ "$(echo "$ratio >= 0.4" | bc -l 2>/dev/null)" = "1" ]; then
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
    score=$(echo "$score + 1.0" | bc -l)
    details="${details}涨幅:1.0(无数据)"
  else
    local chg_score=$(echo "scale=2; if(1 - $abs_chg/5 > 0) 1 - $abs_chg/5 else 0" | bc -l 2>/dev/null)
    [ -z "$chg_score" ] && chg_score=0
    score=$(echo "$score + $chg_score" | bc -l)
    details="${details}涨幅:${chg_score}"
  fi

  # 2️⃣ 量能因子（0-1分）: 成交量相比10日均量的倍数
  if [ -n "$ratio" ] && [ "$ratio" != "0" ] && [ -n "$(echo "$ratio" | grep -E '^[0-9.]')" ]; then
    if [ "$(echo "$ratio >= 1.5" | bc -l 2>/dev/null)" = "1" ]; then
      score=$(echo "$score + 1.0" | bc -l)
      details="${details}|量能:1.0"
    elif [ "$(echo "$ratio >= 1.3" | bc -l 2>/dev/null)" = "1" ]; then
      score=$(echo "$score + 0.5" | bc -l)
      details="${details}|量能:0.5"
    else
      details="${details}|量能:0"
    fi
  else
    details="${details}|量能:0(无数据)"
  fi

  # 3️⃣ 位置因子（0-1分）: 股价处于MA20附近或突破20日高点
  local pos_score=0
  if [ -n "$ma20" ] && [ -n "$price" ] && [ "$(echo "$ma20 > 0" | bc -l 2>/dev/null)" = "1" ]; then
    local dist_to_ma20=$(echo "scale=2; (($price - $ma20) / $ma20) * 100" | bc -l 2>/dev/null | sed 's/^-//')
    # 在MA20附近5%以内 -> 支撑位确认
    if [ -n "$dist_to_ma20" ] && [ "$(echo "$dist_to_ma20 <= 5" | bc -l 2>/dev/null)" = "1" ]; then
      pos_score=0.5
    fi
    # 突破20日高点 -> 强势启动
    if [ -n "$high20" ] && [ "$(echo "$price >= $high20" | bc -l 2>/dev/null)" = "1" ]; then
      pos_score=$(echo "$pos_score + 0.5" | bc -l)
      [ "$(echo "$pos_score > 1.0" | bc -l)" = "1" ] && pos_score=1.0
    fi
    # 回踩20日低点附近（±3%）-> 关键支撑确认（辉哥指定：回踩支撑位加分）
    if [ -n "$low20" ] && [ "$(echo "$low20 > 0" | bc -l 2>/dev/null)" = "1" ]; then
      local signed_dist_low=$(echo "scale=2; ($price - $low20) / $low20 * 100" | bc -l 2>/dev/null)
      local dist_low=$(echo "$signed_dist_low" | sed 's/^-//')
      if [ "$(echo "$dist_low <= 3" | bc -l 2>/dev/null)" = "1" ]; then
        # 取max：支撑分不覆盖其他加分，但在其他分数更低时生效
        [ "$(echo "$pos_score < 0.3" | bc -l 2>/dev/null)" = "1" ] && pos_score=0.3
      fi
    fi
    # 远离MA20超过10% -> 追高风险（仅上穿方向惩罚，下穿是超跌不罚）
    if [ -n "$dist_to_ma20" ] && [ "$(echo "$dist_to_ma20 > 10" | bc -l 2>/dev/null)" = "1" ]; then
      local signed_pos=$(echo "scale=2; ($price - $ma20) / $ma20 * 100" | bc -l 2>/dev/null)
      [ "$(echo "$signed_pos > 10" | bc -l 2>/dev/null)" = "1" ] && pos_score=$(echo "$pos_score * 0.5" | bc -l 2>/dev/null)
    fi
  fi
  score=$(echo "$score + $pos_score" | bc -l)
  details="${details}|位置:$pos_score"

  # 4️⃣ 趋势因子（0-1分）: MA20方向 + 高位约束
  local trend_score=0
  # 高位降级：股价偏离MA20超过10%时，趋势因子降级
  local dist_pct=""
  if [ -n "$ma20" ] && [ -n "$price" ] && [ "$(echo "$ma20 > 0" | bc -l 2>/dev/null)" = "1" ]; then
    dist_pct=$(echo "scale=2; ($price - $ma20) / $ma20 * 100" | bc -l 2>/dev/null)
  fi

  if [ -n "$ma20" ] && [ -f "$cache" ]; then
    local total_lines=$(wc -l < "$cache" 2>/dev/null)
    if [ "$total_lines" -ge 25 ]; then
      # 用5天前的收盘价估算5日前MA20（尾-5行到尾-24行）
      local prev_ma20=$(tail -25 "$cache" | head -20 | awk '{s+=$1} END{printf "%.2f", s/20}' 2>/dev/null)
      if [ -n "$prev_ma20" ] && [ "$(echo "$prev_ma20 > 0" | bc -l 2>/dev/null)" = "1" ]; then
        local trend_slope=$(echo "scale=2; ($ma20 - $prev_ma20) / $prev_ma20 * 100" | bc -l 2>/dev/null)
        # 基础趋势判定
        if [ "$(echo "$trend_slope > 1.0" | bc -l 2>/dev/null)" = "1" ]; then
          trend_score=1.0
        elif [ "$(echo "$trend_slope < -1.0" | bc -l 2>/dev/null)" = "1" ]; then
          trend_score=0.0
        else
          trend_score=0.5  # 走平
        fi
        # 高位约束：偏离MA20超过10%时趋势分折半（仅上穿，远离均线=追高风险）
        if [ -n "$dist_pct" ] && [ "$(echo "$dist_pct > 10" | bc -l 2>/dev/null)" = "1" ]; then
          trend_score=$(echo "$trend_score * 0.5" | bc -l 2>/dev/null)
        fi
        # 短期转弱：股价跌破MA5时趋势分折半
        if [ -n "$ma5" ] && [ "$(echo "$ma5 > 0" | bc -l 2>/dev/null)" = "1" ] && [ "$(echo "$price < $ma5" | bc -l 2>/dev/null)" = "1" ]; then
          trend_score=$(echo "$trend_score * 0.5" | bc -l 2>/dev/null)
        fi
      fi
    elif [ "$(echo "$price > $ma20" | bc -l 2>/dev/null)" = "1" ]; then
      # 缓存不足时降级判断：price>MA20视为弱势向上
      trend_score=0.5
    fi
  fi
  score=$(echo "$score + $trend_score" | bc -l)
  details="${details}|趋势:$trend_score"

  # 5️⃣ 盘前/盘后缓冲因子（0-1分）：量能不可用时，用日线趋势替代
  local buffer_score=0
  if echo "$details" | grep -q '量能:0'; then
    # A: 均线多头排列 MA5>MA10>MA20
    if [ -n "$ma5" ] && [ -n "$ma10" ] && [ -n "$ma20" ]; then
      if [ "$(echo "$ma5 > $ma10" | bc -l 2>/dev/null)" = "1" ] && [ "$(echo "$ma10 > $ma20" | bc -l 2>/dev/null)" = "1" ]; then
        buffer_score=$(echo "$buffer_score + 0.5" | bc -l)
      fi
    fi
    # B: 股价维持MA5之上（短线未破位）
    if [ -n "$ma5" ] && [ -n "$price" ] && [ "$(echo "$price > $ma5" | bc -l 2>/dev/null)" = "1" ]; then
      buffer_score=$(echo "$buffer_score + 0.25" | bc -l)
    fi
    # C: MACD零轴上方
    if [ -n "$dif" ] && [ "$(echo "$dif > 0" | bc -l 2>/dev/null)" = "1" ]; then
      buffer_score=$(echo "$buffer_score + 0.25" | bc -l)
    fi
    if [ "$(echo "$buffer_score > 0" | bc -l 2>/dev/null)" = "1" ]; then
      details="${details}|缓:$buffer_score"
      score=$(echo "$score + $buffer_score" | bc -l)
    fi
  fi

  # 前导零修复
  score=$(echo "$score" | sed 's/^\./0./;s/^-\./-0./')
  echo "$score|$details"
}

# --------------- 信号级别 ---------------

classify_level() {
  local abs=$(echo "$1" | sed 's/^-//' | tr -d '%')
  [ "$(echo "$abs > 7" | bc -l 2>/dev/null)" = "1" ] && { echo "L3_URGENT"; return; }
  [ "$(echo "$abs > 4" | bc -l 2>/dev/null)" = "1" ] && { echo "L2_STRONG"; return; }
  [ "$(echo "$abs > 2" | bc -l 2>/dev/null)" = "1" ] && { echo "L1_NORMAL"; return; }
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
  local net_vote=$(echo "$buy_vote - $sell_vote" | bc -l 2>/dev/null)
  
  # 卖出优先：sell_vote >= 0.60 → 卖出确认
  if [ "$(echo "$sell_vote >= 0.80" | bc -l 2>/dev/null)" = "1" ]; then
    verdict="卖出确认-减仓"; strength=-3
  elif [ "$(echo "$sell_vote >= 0.60" | bc -l 2>/dev/null)" = "1" ]; then
    verdict="卖出预警-关注"; strength=-2
  elif [ "$(echo "$sell_vote >= 0.35" | bc -l 2>/dev/null)" = "1" ] && [ "$(echo "$buy_vote < 0.30" | bc -l 2>/dev/null)" = "1" ]; then
    verdict="卖出预警-关注"; strength=-1
  fi
  
  # 买入判定（仅在卖出未触发时）
  if [ "$strength" -eq 0 ]; then
    if [ "$(echo "$buy_vote >= 0.80 && $sell_vote < 0.30" | bc -l 2>/dev/null)" = "1" ]; then
      verdict="三重共振-出手"; strength=3
    elif [ "$(echo "$buy_vote >= 0.55 && $sell_vote < 0.35" | bc -l 2>/dev/null)" = "1" ]; then
      verdict="双重确认-可参与"; strength=2
    elif [ "$(echo "$buy_vote >= 0.30" | bc -l 2>/dev/null)" = "1" ]; then
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
  if [ "$strength" -ge 2 ] && [ "$(echo "$volume_factor >= 0.8" | bc -l 2>/dev/null)" = "1" ]; then
    :  # 放量确认，维持原级
  elif [ "$strength" -ge 2 ] && [ "$(echo "$volume_factor < 0.3" | bc -l 2>/dev/null)" = "1" ]; then
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
    gap_down)              echo "B|-0.25|ALL" ;;
    rsi_overbought)        echo "B|-0.25|STRONG_UP,WEAK_UP" ;;
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
    chip_deviation_high)   echo "C|-0.15|STRONG_UP,WEAK_UP" ;;
    underperform_sector)   echo "C|-0.15|ALL" ;;
    volume_surge)          echo "C|-0.10|ALL" ;;
    
    # ── Tier-C: 偏离度/换手率风险信号 ──
    ma5_gap)               echo "C|-0.20|STRONG_UP,WEAK_UP,CHOP_UP" ;;
    turnover_abnormal)     echo "C|-0.25|STRONG_UP,WEAK_UP,CHOP_UP" ;;
    
    # ── Tier-C: 估值预警 ──
    pe_overvalued)         echo "C|-0.25|STRONG_UP,WEAK_UP,CHOP_UP" ;;
    pe_extreme)            echo "C|-0.30|STRONG_UP,WEAK_UP,CHOP_UP" ;;
    
    # ── Tier-C: 获利盘高位风险（从Z升级为C）──
    chip_profit_high)      echo "C|-0.15|STRONG_UP,WEAK_UP,CHOP_UP" ;;
    
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
    
    if [ "$(echo "$weight > 0" | bc -l 2>/dev/null)" = "1" ]; then
      buy_vote=$(echo "$buy_vote + $weight" | bc -l)
      ((buy_count++))
      case "$tier" in
        A) ((tier_a_buy++)) ;;
        B) ((tier_b_buy++)) ;;
        C) ((tier_c_buy++)) ;;
      esac
    elif [ "$(echo "$weight < 0" | bc -l 2>/dev/null)" = "1" ]; then
      local abs_w=$(echo "$weight" | sed 's/^-//')
      sell_vote=$(echo "$sell_vote + $abs_w" | bc -l)
      ((sell_count++))
      case "$tier" in
        A) ((tier_a_sell++)) ;;
        B) ((tier_b_sell++)) ;;
        C) ((tier_c_sell++)) ;;
      esac
    fi
  done
  
  # morph_score = buy_vote - sell_vote（限幅-1.0~+1.0）
  local morph_score=$(echo "$buy_vote - $sell_vote" | bc -l 2>/dev/null)
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
        local pulled_up=$(echo "$morph_score + $bonus" | bc -l 2>/dev/null)
        [ "$(echo "$pulled_up > 1.0" | bc -l 2>/dev/null)" = "1" ] && pulled_up=1.0
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
  local dif=$(calc_dif "$cache")
  local prev_dif=$(calc_prev_dif "$cache")
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
    [ -n "$result" ] && signals+=("$result")
  done
  
  # ── PE 估值判定（内联，避免子 shell 中 $RAW 不可见）──
  [ -n "$pe_ttm" ] && [ "$pe_ttm" != "0" ] && {
    if [ "$(echo "$pe_ttm > 300" | bc 2>/dev/null)" = "1" ]; then
      signals+=('{"rule":"pe_extreme","direction":"sell","note":"PE_TTM='$pe_ttm'极高估值,风险极大","strength":"high"}')
    elif [ "$(echo "$pe_ttm > 120" | bc 2>/dev/null)" = "1" ]; then
      signals+=('{"rule":"pe_overvalued","direction":"sell","note":"PE_TTM='$pe_ttm'偏高估值","strength":"medium"}')
    elif [ "$(echo "$pe_ttm > 80" | bc 2>/dev/null)" = "1" ]; then
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
  local total_score_ext=$(echo "$state_score + $morph_score" | bc -l 2>/dev/null)
  total_score_ext=$(echo "$total_score_ext" | sed 's/^\./0./;s/^-\./-0./')
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
  # 换手率($39)（全局变量，规则函数可访问）
  STOCK_TURNOVER=$(echo "$d" | awk -F'~' '{print $39}' 2>/dev/null)
  [ -z "$STOCK_TURNOVER" ] && STOCK_TURNOVER=0
  # 量比：gtimg字段位置不统一（深交所字段50，上交所字段47），自行计算
  # 量比 = (今日成交量/已交易分钟数) / (近5日均量/240)
  local cache="$CACHE_DIR/${code}.day"
  local avg5v=$(avgvol_n "$cache" 5)
  if [ -n "$avg5v" ] && [ "$(echo "$avg5v > 0" | bc -l 2>/dev/null)" = "1" ]; then
    # 已交易分钟数：从gtimg时间字段(30)推算
    local now_str=$(echo "$d" | awk -F'~' '{print $31}' 2>/dev/null)
    local hour=${now_str:8:2}
    local min=${now_str:10:2}
    # 9:30开盘，上午11:30休市，下午13:00开盘
    local total_min=0
    if [ -n "$hour" ] && [ "$hour" -ge 9 ]; then
      if [ "$hour" -lt 12 ]; then
        # 上午盘
        total_min=$(( (hour - 9) * 60 + min - 30 ))
      else
        # 下午盘
        total_min=$(( 120 + (hour - 13) * 60 + min ))
      fi
    fi
    [ "$total_min" -lt 1 ] && total_min=1
    VOL_RATIO_GLOBAL=$(echo "scale=2; ($vol / $total_min) / ($avg5v / 240)" | bc -l 2>/dev/null)
  else
    VOL_RATIO_GLOBAL=0
  fi
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
# v6.0 后处理：修复 signals 数组中缺失的逗号 + 前导零
python3 -c "
import re, sys
with open('$REPORT_FILE') as f:
    content = f.read()
# 修复: }{  →  },{  （JSON对象间缺逗号）
content = re.sub(r'\}\\s*\{', '},{', content)
# 修复: :. → :0. （bc输出.5而不是0.5）
content = re.sub(r':\.(\d)', r':0.\1', content)
with open('$REPORT_FILE', 'w') as f:
    f.write(content)
" 2>/dev/null
# 后处理：计算概念板块相对强度
python3 "$SIGNAL_DIR/concept_relative_strength.py" 2>/dev/null
# 合并概念信号到引擎输出，生成统一信号文件
python3 "$SIGNAL_DIR/merge_concept_signals.py" "$REPORT_FILE" 2>/dev/null

# 清理旧临时信号文件（保留最近5个）
ls -t /tmp/stock_signals_*.json 2>/dev/null | tail -n +6 | xargs rm -f 2>/dev/null
