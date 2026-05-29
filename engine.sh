#!/bin/bash
# ==========================================================
# 信号引擎 V3 — 数据预取架构
# 1. 一次拉取全池行情 (gtimg 单次 curl)
# 2. 从缓存计算所有衍生指标（MA/MACD/量比）
# 3. 每条规则收到完整数据 struct（零网络调用）
# ==========================================================

SIGNAL_DIR="$(cd "$(dirname "$0")" && pwd)"
RULES_DIR="$SIGNAL_DIR/rules"
CACHE_DIR="$SIGNAL_DIR/cache"
WORKSPACE="/root/.openclaw/workspace"
mkdir -p "$CACHE_DIR"

# --------------- 数据预取层 ---------------

get_all_codes() {
  # 合并所有采集来源，经过去重
  {
    printf "000001\n399001\n399006\n"
    bash "$WORKSPACE/scripts/tools.sh" holdings 2>/dev/null | awk '{print $1}'
    bash "$WORKSPACE/scripts/tools.sh" history  2>/dev/null | awk '{print $1}'
    echo "516640 159667 159858 159928 512400 688008 300308 300394 002230 300750 300502 600522 300456 002281 300620 601138 000977 300476 000034 002837 300499 301018 300738 300383 001309 300475 002119 300302 300661 688798 300223 603881 300857 000032 002335 600602 600118 002025 300045 688568 300762 600343 300455 688523 301306 002465 600391 600592 301005 000901 002682 600151 000551 300265 002361 003009 600345 002151" | tr ' ' '\n'
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
  # 分批拉取全池实时行情（每批60只）
  local all="${1:-}" batch="" result="" count=0
  while read code; do
    [ -z "$code" ] && continue
    [[ $code == 6* || $code == "000001" ]] && batch="${batch}sh${code}," || batch="${batch}sz${code},"
    ((count++))
    if [ "$count" -ge 60 ]; then
      result="${result}$(curl -s --max-time 10 "https://qt.gtimg.cn/q=${batch%,}" 2>/dev/null | iconv -f GBK -t UTF-8 2>/dev/null | sed 's/";v_/";\nv_/g')
"
      batch=""; count=0
    fi
  done < <(get_all_codes)
  [ -n "$batch" ] && result="${result}$(curl -s --max-time 10 "https://qt.gtimg.cn/q=${batch%,}" 2>/dev/null | iconv -f GBK -t UTF-8 2>/dev/null | sed 's/";v_/";\nv_/g')
"
  echo "$result"
}

# 均线计算
ma_n() { local f="$1" n="$2"; [ -f "$f" ] && [ "$(wc -l < "$f")" -ge "$n" ] && tail -"$n" "$f" | awk '{s+=$1} END{printf "%.2f", s/'$n'}'; }

# 均成交量
avgvol_n() { local f="$1" n="$2"; [ -f "$f" ] && [ "$(wc -l < "$f")" -ge "$n" ] && tail -"$n" "$f" | awk '{s+=$2} END{printf "%.0f", s/'$n'}'; }

# 近N日最高/最低价
high_n() { local f="$1" n="$2"; [ -f "$f" ] && [ "$(wc -l < "$f")" -ge "$n" ] && tail -"$n" "$f" | awk 'max==""||$1>max{max=$1} END{printf "%.2f", max}'; }
low_n()  { local f="$1" n="$2"; [ -f "$f" ] && [ "$(wc -l < "$f")" -ge "$n" ] && tail -"$n" "$f" | awk 'min==""||$1<min{min=$1} END{printf "%.2f", min}'; }

# EMA (指数移动平均) — 用于 MACD
calc_ema() {
  local f="$1" n="$2"
  [ ! -f "$f" ] && return
  local total=$(wc -l < "$f")
  [ "$total" -lt "$n" ] && return
  local k=$(echo "scale=6; 2/($n+1)" | bc -l)
  local ema=$(head -1 "$f" | awk '{print $1}')
  head -"$n" "$f" | tail -$(($n - 1)) | while read p v; do
    # 递归 EMA
    echo ""
  done
  # 用 python 一次性算 EMA（bash 递归太慢）
  python3 -c "
f=open('$f'); lines=f.readlines(); f.close()
n=$n
prices=[float(l.split()[0]) for l in lines[-n:]]
ema=prices[0]
k=2/(n+1)
for p in prices[1:]:
    ema=p*k+ema*(1-k)
print(f'{ema:.2f}')
" 2>/dev/null
}

# MACD DIF (EMA12 - EMA26)
calc_dif() {
  local f="$1"
  [ ! -f "$f" ] || [ "$(wc -l < "$f")" -lt 26 ] && return
  local ema12=$(calc_ema "$f" 12)
  local ema26=$(calc_ema "$f" 26)
  [ -z "$ema12" ] || [ -z "$ema26" ] && return
  echo "scale=2; $ema12 - $ema26" | bc -l 2>/dev/null | sed 's/^\./0./;s/^-\./-0./'
}

calc_prev_dif() {
  local f="$1"
  [ ! -f "$f" ] || [ "$(wc -l < "$f")" -lt 27 ] && return
  # 用前一天的缓存（去掉最后一行）
  local tmp=$(mktemp)
  head -n -1 "$f" > "$tmp"
  local result=$(calc_dif "$tmp")
  rm -f "$tmp"
  echo "$result"
}

# =============== P0：信号评分卡 ===============
# 四因子质量评分（每项0-1分，总分0-4分）
# 阈值：≥2.5分保留原信号等级，<2.5分降一级
# =============================================

compute_signal_quality() {
  local change="$1" ratio="$2" price="$3" ma20="$4" high20="$5" low20="$6" cache="$7"
  # 前导零修复（bc -l 输出的 .5 → 0.5）
  ratio=$(echo "$ratio" | sed 's/^\./0./')
  price=$(echo "$price" | sed 's/^\./0./')
  ma20=$(echo "$ma20" | sed 's/^\./0./')
  high20=$(echo "$high20" | sed 's/^\./0./')
  low20=$(echo "$low20" | sed 's/^\./0./')
  echo "[DBG] compute_signal_quality: chg=$change ratio=$ratio price=$price ma20=$ma20" >&2
  local score=0.0 details=""

  # 1️⃣ 涨幅因子（0-1分）: 触发时当日涨幅越小得分越高
  local abs_chg=$(echo "$change" | sed 's/^-//' | tr -d '%')
  if [ -z "$abs_chg" ] || [ "$abs_chg" = "0" ]; then
    score=$(echo "$score + 0.5" | bc -l)
    details="${details}涨幅:0.5(无数据)"
  elif [ "$(echo "$abs_chg <= 1" | bc -l 2>/dev/null)" = "1" ]; then
    score=$(echo "$score + 1.0" | bc -l)
    details="${details}涨幅:1.0"
  elif [ "$(echo "$abs_chg <= 3" | bc -l 2>/dev/null)" = "1" ]; then
    score=$(echo "$score + 0.5" | bc -l)
    details="${details}涨幅:0.5"
  else
    details="${details}涨幅:0"
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
    # 远离MA20超过10% -> 追高风险
    if [ -n "$dist_to_ma20" ] && [ "$(echo "$dist_to_ma20 > 10" | bc -l 2>/dev/null)" = "1" ]; then
      pos_score=$(echo "$pos_score * 0.5" | bc -l 2>/dev/null)
    fi
  fi
  score=$(echo "$score + $pos_score" | bc -l)
  details="${details}|位置:$pos_score"

  # 4️⃣ 趋势因子（0-1分）: MA20方向 + 高位约束
  local trend_score=0
  local dist_pct=""
  if [ -n "$ma20" ] && [ -n "$price" ] && [ "$(echo "$ma20 > 0" | bc -l 2>/dev/null)" = "1" ]; then
    dist_pct=$(echo "scale=2; ($price - $ma20) / $ma20 * 100" | bc -l 2>/dev/null)
  fi

  if [ -n "$ma20" ] && [ -f "$cache" ]; then
    local total_lines=$(wc -l < "$cache" 2>/dev/null)
    if [ "$total_lines" -ge 25 ]; then
      local prev_ma20=$(tail -25 "$cache" | head -20 | awk '{s+=\$1} END{printf "%.2f", s/20}' 2>/dev/null)
      if [ -n "$prev_ma20" ] && [ "$(echo "$prev_ma20 > 0" | bc -l 2>/dev/null)" = "1" ]; then
        local trend_slope=$(echo "scale=2; ($ma20 - $prev_ma20) / $prev_ma20 * 100" | bc -l 2>/dev/null)
        if [ "$(echo "$trend_slope > 0.3" | bc -l 2>/dev/null)" = "1" ]; then
          trend_score=1.0
        elif [ "$(echo "$trend_slope < -0.3" | bc -l 2>/dev/null)" = "1" ]; then
          trend_score=0.0
        else
          trend_score=0.5
        fi
        if [ -n "$dist_pct" ] && [ "$(echo "$dist_pct > 10" | bc -l 2>/dev/null)" = "1" ]; then
          trend_score=$(echo "$trend_score * 0.5" | bc -l 2>/dev/null)
        fi
      fi
    elif [ "$(echo "$price > $ma20" | bc -l 2>/dev/null)" = "1" ]; then
      trend_score=0.5
    fi
  fi
  score=$(echo "$score + $trend_score" | bc -l)
  details="${details}|趋势:$trend_score"

  # 5️⃣ 盘前/盘后缓冲因子（0-1分）: 当量能分=0（非活跃时段）时，用日线趋势替代
  # 检查最近5日的量价排列，识别"均线多头+缩量不破5日线"的整理形态
  local buffer_score=0
  if echo "$details" | grep -q '量能:0'; then
    # 条件A: MA5 > MA10 > MA20（均线多头排列）→ 趋势健康
    if [ -n "$ma5" ] && [ -n "$ma10" ] && [ -n "$ma20" ]; then
      if [ "$(echo "$ma5 > $ma10" | bc -l 2>/dev/null)" = "1" ] && [ "$(echo "$ma10 > $ma20" | bc -l 2>/dev/null)" = "1" ]; then
        buffer_score=$(echo "$buffer_score + 0.5" | bc -l)
      fi
    fi
    # 条件B: 当前价 > MA5（短线未破位）
    if [ -n "$ma5" ] && [ -n "$price" ] && [ "$(echo "$price > $ma5" | bc -l 2>/dev/null)" = "1" ]; then
      buffer_score=$(echo "$buffer_score + 0.25" | bc -l)
    fi
    # 条件C: MACD零轴上方（DIF > 0）
    if [ -n "$dif" ] && [ "$(echo "$dif > 0" | bc -l 2>/dev/null)" = "1" ]; then
      buffer_score=$(echo "$buffer_score + 0.25" | bc -l)
    fi
    if [ "$(echo "$buffer_score > 0" | bc -l 2>/dev/null)" = "1" ]; then
      details="${details}|缓:$buffer_score"
      score=$(echo "$score + $buffer_score" | bc -l)
    fi
  fi

  echo "$score|$details"
}

# --------------- 信号级别 ---------------

classify_level() {
  local abs=$(echo "$1" | sed 's/^-//' | tr -d '%')
  [ "$(echo "$abs > 7" | bc -l 2>/dev/null)" = "1" ] && { echo "L3_URGENT"; return; }
  [ "$(echo "$abs > 4" | bc -l 2>/dev/null)" = "1" ] && { echo "L2_STRONG"; return; }
  [ "$(echo "$abs > 2" | bc -l 2>/dev/null)" = "1" ] && { echo "L1_NORMAL"; return; }
}

# --------------- 共振计算（含信号质量评分降级） ---------------

calc_resonance() {
  local buy=0 sell=0 quality_score="${QUALITY_SCORE:-3.0}"
  local has_morphology=0 has_volume=0
  for sig in "$@"; do
    # 买入方向判定
    local is_buy=0 is_sell=0
    [[ $sig == *"buy_signal"* || $sig == *"bullish"* || $sig == *"strong_hold"* \
        || $sig == *"golden_cross"* || $sig == *"breakout_up"* \
        || $sig == *"washout"* ]] && is_buy=1
    # 卖出方向
    [[ $sig == *"sell_signal"* || $sig == *"bearish"* \
        || $sig == *"death_cross"* || $sig == *"should_rise_fail"* \
        || $sig == *"bearish_warn"* ]] && is_sell=1
    [ "$is_buy" -eq 1 ] && ((buy++))
    [ "$is_sell" -eq 1 ] && ((sell++))
    # 信号类型分类（买入信号才参与维度判定）
    if [ "$is_buy" -eq 1 ]; then
      local rule=$(echo "$sig" | grep -o '"rule":"[^"]*"' | cut -d'"' -f4)
      case "$rule" in
        ma_*|bullish_arr*|macd_bottom_div*|macd_above_zero*)
          has_morphology=1 ;;
        breakout_up*|2b_fake_breakdown*|breakout*|2b_*)
          has_morphology=1 ;;
        hammer*|red_three*|gap_up*)
          has_morphology=1 ;;
        should_fall_strong*)
          has_morphology=1 ;;
        vol_*)
          has_volume=1 ;;
      esac
    fi
  done
  
  local verdict="观望" strength=0
  
  # 三重共振：≥3个买入信号 + 形态确认 + 量能确认
  if [ "$buy" -ge 3 ] && [ "$has_morphology" -eq 1 ] && [ "$has_volume" -eq 1 ]; then
    verdict="三重共振-出手"; strength=3
  elif [ "$buy" -ge 3 ]; then
    # 数量够但缺形态或量能 → 降级
    verdict="双重确认-可参与"; strength=2
  elif [ "$buy" -eq 2 ] && [ "$has_morphology" -eq 1 ] && [ "$has_volume" -eq 1 ]; then
    verdict="双重确认-可参与"; strength=2
  elif [ "$buy" -eq 2 ]; then
    verdict="单一信号-观察"; strength=1
  elif [ "$buy" -eq 1 ]; then
    verdict="单一信号-观察"; strength=1
  fi
  
  # 卖出信号优先
  [ "$sell" -ge 2 ] && { verdict="卖出确认-减仓"; strength=-2; }
  [ "$sell" -eq 1 ] && [ "$buy" -lt 2 ] && { verdict="卖出预警-关注"; strength=-1; }
  
  # P0评分卡降级：质量分<2.5时降低买入信号等级
  if [ "$buy" -ge 2 ] && [ "$(echo "$quality_score < 2.5" | bc -l 2>/dev/null)" = "1" ]; then
    # score不足→降一级：三重→双重, 双重→单一
    if [ "$strength" -ge 3 ]; then
      verdict="双重确认-可参与(评分降级)"; strength=2
    elif [ "$strength" -eq 2 ]; then
      verdict="单一信号-观察(评分降级)"; strength=1
    fi
  fi
  
  echo "{\"verdict\":\"$verdict\",\"buy_signals\":$buy,\"sell_signals\":$sell,\"strength\":$strength,\"morphology\":$has_morphology,\"volume\":$has_volume,\"quality_score\":$quality_score}"
}

# --------------- 主评估流程 ---------------

evaluate() {
  local code="$1" name="$2" price="$3" change="$4" open="$5" high="$6" low="$7" yclose="$8" vol="$9"
  local cache="$CACHE_DIR/${code}.day"
  
  # 从缓存预计算所有衍生指标
  local ma5=$(ma_n "$cache" 5)
  local ma10=$(ma_n "$cache" 10)
  local ma20=$(ma_n "$cache" 20)
  local ma60=$(ma_n "$cache" 60)
  local avg10v=$(avgvol_n "$cache" 10)
  local high20=$(high_n "$cache" 20)
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
  
  # 指数代码始终输出（供smart_monitor市场过滤器使用）
  if [ ${#signals[@]} -eq 0 ]; then
    [[ $code == "000001" || $code == "399001" || $code == "399006" ]] || return
  fi
  
  # P0：计算四因子信号质量评分
  local vol_ratio=$(echo "scale=2; if($avg10v > 0) $vol / $avg10v else 0" | bc -l 2>/dev/null)
  local score_result=$(compute_signal_quality "$change" "$vol_ratio" "$price" "$ma20" "$high20" "$low20" "$cache")
  local quality_score=$(echo "$score_result" | cut -d'|' -f1)
  local score_details=$(echo "$score_result" | cut -d'|' -f2)
  [ -z "$quality_score" ] && quality_score=3.0
  
  # P0v2：从signals[]中扫描已有规则的形态信号
  local morph_score=$(scan_morphology_signals "${signals[@]}")
  [ -z "$morph_score" ] && morph_score=0
  
  # 形态因子不计入4分上限，额外加到总分
  local total_score_ext=$(echo "$quality_score + $morph_score" | bc -l 2>/dev/null)
  [ -z "$total_score_ext" ] && total_score_ext=$quality_score

  # 盘前缓冲：量能不可用时，用日线趋势替代评分
  if echo "$score_details" | grep -q '量能:0'; then
    local buffer_score=0.0
    # 均线多头排列
    if [ -n "$ma5" ] && [ -n "$ma10" ] && [ -n "$ma20" ] && [ "$(echo "$ma5 > $ma10" | bc -l 2>/dev/null)" = "1" ] && [ "$(echo "$ma10 > $ma20" | bc -l 2>/dev/null)" = "1" ]; then
      buffer_score=$(echo "$buffer_score + 0.5" | bc -l)
    fi
    # 股价在MA5之上
    if [ -n "$ma5" ] && [ -n "$price" ] && [ "$(echo "$price > $ma5" | bc -l 2>/dev/null)" = "1" ]; then
      buffer_score=$(echo "$buffer_score + 0.25" | bc -l)
    fi
    # MACD零轴上方
    if [ -n "$dif" ] && [ "$(echo "$dif > 0" | bc -l 2>/dev/null)" = "1" ]; then
      buffer_score=$(echo "$buffer_score + 0.25" | bc -l)
    fi
    if [ "$(echo "$buffer_score > 0" | bc -l 2>/dev/null)" = "1" ]; then
      total_score_ext=$(echo "$total_score_ext + $buffer_score" | bc -l)
      score_details="${score_details}|缓:$buffer_score"
    fi
  fi
  
  # 注入质量评分到共振计算（用4因子分做降级判定）
  QUALITY_SCORE=$quality_score
  local resonance=$(calc_resonance "${signals[@]}")
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
  \"quality_score\":$quality_score,
  \"morph_score\":$morph_score,
  \"total_score_ext\":$total_score_ext,
  \"score_details\":\"$score_details\",
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
RAW=$(fetch_bulk)

# --------------- 计算概念板块基准 ---------------
timeout 15 bash "$SIGNAL_DIR/compute_concept_benchmarks.sh" compute >/dev/null 2>&1
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
  
  [ -z "$price" ] || [ "$price" = "0.000" ] && continue
  
  result=$(evaluate "$code" "$name" "$price" "$change" "$open" "$high" "$low" "$yclose" "$vol")
  [ -n "$result" ] && {
    $first || echo "," >> "$REPORT_FILE"
    echo "$result" >> "$REPORT_FILE"
    first=false
  }
done < <(get_all_codes)

echo "]" >> "$REPORT_FILE"
echo "signal_file=$REPORT_FILE"
# 后处理：计算概念板块相对强度
python3 "$SIGNAL_DIR/concept_relative_strength.py" 2>/dev/null
# 合并概念信号到引擎输出，生成统一信号文件
python3 "$SIGNAL_DIR/merge_concept_signals.py" "$REPORT_FILE" 2>/dev/null

# 清理旧临时信号文件（保留最近5个）
ls -t /tmp/stock_signals_*.json 2>/dev/null | tail -n +6 | xargs rm -f 2>/dev/null
