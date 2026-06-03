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
  {
    printf "000001\n399001\n399006\n"
    bash "$WORKSPACE/scripts/tools.sh" holdings 2>/dev/null | awk '{print $1}'
    bash "$WORKSPACE/scripts/tools.sh" history  2>/dev/null | awk '{print $1}'
    echo "516640 159667 159858 159928 512400 688008 300308 300394 002230 300750 300502 600522 300456 002281 300620 601138 000977 300476 000034 002837 300499 301018 300738 300383 001309 300475 002119 300302 300661 688798 300223 603881 300857 000032 002335 600602 600118 002025 300045 688568 300762 600343 300455 688523 301306 002465 600391 600592 301005 000901 002682 600151 000551 300265 002361 003009 600345 002151 002371 002384 002463 002553 002896 002916 002938 300124 300364 300442 300450 300660 300809 301308 600183 600580 600835 603203 603232 603256 603688 603986 603990 688012 688525 000636 000960 000988 001314 002202 002881 300113 600029 600115 600309 600961 601111 601869 603232 688041" | tr ' ' '\n'
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
  # 一次性拉取全池实时行情（单次curl，~60KB，10秒内完成）
  local all="${1:-}" batch=""
  while read code; do
    [ -z "$code" ] && continue
    [[ $code == 6* || $code == "000001" ]] && batch="${batch}sh${code}," || batch="${batch}sz${code},"
  done < <(get_all_codes)
  batch="${batch%,}"
  curl -s --max-time 20 "https://qt.gtimg.cn/q=$batch" 2>/dev/null | iconv -f GBK -t UTF-8 2>/dev/null | sed 's/";v_/";\nv_/g'
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

# --------------- 共振计算（含信号质量评分降级） ---------------

calc_resonance() {
  local buy=0 sell=0 quality_score="${QUALITY_SCORE:-3.0}"
  local has_morphology=0 has_volume=0
  for sig in "$@"; do
    # 买入方向判定（检查direction字段和关键词）
    local is_buy=0 is_sell=0
    local sig_dir=$(echo "$sig" | grep -o '"direction":"[^"]*"' | cut -d'"' -f4)
    case "$sig_dir" in
      buy_signal|bullish*|strong_hold|breakout|up|breakout_up|bullish_urgent)
        is_buy=1 ;;
      sell_signal|bearish*|breakdown|bearish_warn|bearish_urgent)
        is_sell=1 ;;
      exclude_buy|no_add|suspend_all_buy|risk_mgmt)
        # 中性/管理型信号不计入 ;;
    esac
    # 规则名也参与判定（兼容旧输出信号）
    [[ $sig == *"golden_cross"* || $sig == *"washout"* || $sig == *"2b_fake_breakdown"* ]] && is_buy=1
    [[ $sig == *"death_cross"* || $sig == *"should_rise_fail"* || $sig == *"2b_fake_breakout"* ]] && is_sell=1
    # v3.0: MACD死叉/持续死叉=卖出信号
    [[ $sig == *"macd_death_cross"* || $sig == *"macd_death_ongoing"* ]] && is_sell=1
    [ "$is_buy" -eq 1 ] && ((buy++))
    [ "$is_sell" -eq 1 ] && ((sell++))
    # 信号类型分类（买入信号才参与维度判定）
    if [ "$is_buy" -eq 1 ]; then
      local rule=$(echo "$sig" | grep -o '"rule":"[^"]*"' | cut -d'"' -f4)
      case "$rule" in
        ma_*|bullish_arr*|macd_bottom_div*)
          has_morphology=1 ;;
        breakout_up*|2b_fake_breakdown*|breakout*|2b_*)
          has_morphology=1 ;;
        hammer*|red_three*|gap_up*|historical_breakthrough*)
          has_morphology=1 ;;
        should_fall_strong*)
          has_morphology=1 ;;
        shrink_then_breakout*|outperform_sector*)
          has_morphology=1 ;;
        vol_*|turnover_*)
          has_volume=1 ;;
      esac
    fi
  done
  
  # 共振修正：顶背离+天量出货 → 买入形态信号作废
  local has_sell_div=0 has_heavy_vol=0
  for sig in "$@"; do
    if echo "$sig" | grep -qE '"(macd_top_div|shooting_star|fake_breakthrough)"'; then
      has_sell_div=1
    fi
    if echo "$sig" | grep -qE '"(turnover_abnormal|turnover_high|volume_surge)"'; then
      has_heavy_vol=1
    fi
  done
  if [ "$has_sell_div" -eq 1 ] && [ "$has_heavy_vol" -eq 1 ]; then
    has_morphology=0
  fi
  
  local verdict="观望" strength=0
  
  # 三重共振：≥3个买入信号 + 形态确认 + 量能确认（最高确定性）
  if [ "$buy" -ge 3 ] && [ "$has_morphology" -eq 1 ] && [ "$has_volume" -eq 1 ]; then
    verdict="三重共振-出手"; strength=3
  # 双重确认：≥2买入信号 + 形态确认（放宽量能要求，实战可达）
  elif [ "$buy" -ge 2 ] && [ "$has_morphology" -eq 1 ]; then
    verdict="双重确认-可参与"; strength=2
  # 数量够但缺形态 → 观察
  elif [ "$buy" -ge 2 ]; then
    verdict="单一信号-观察"; strength=1
  elif [ "$buy" -eq 1 ]; then
    verdict="单一信号-观察"; strength=1
  fi
  
  # 卖出信号优先
  [ "$sell" -ge 2 ] && { verdict="卖出确认-减仓"; strength=-2; }
  [ "$sell" -eq 1 ] && [ "$buy" -lt 2 ] && { verdict="卖出预警-关注"; strength=-1; }

  # 🔴 v3.0 多空冲突降级: buy≥2且sell≥1 → 强制降一级
  if [ "$buy" -ge 2 ] && [ "$sell" -ge 1 ] && [ "$strength" -ge 2 ]; then
    if [ "$strength" -ge 3 ]; then
      verdict="双重确认-可参与(冲突降级)"; strength=2
    elif [ "$strength" -eq 2 ]; then
      verdict="单一信号-观察(冲突降级)"; strength=1
    fi
  fi
  
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

# =============== 形态因子评分 ===============
# 扫描所有已触发的规则信号，汇总形态特征综合打分
# 覆盖现有所有规则文件中的形态模式
# 分值范围：-1.0（极差） ~ +1.0（极佳）
# ============================================

scan_morphology_signals() {
  local bull_score=0 bear_score=0 multi_bonus=0
  local bull_count=0 bear_count=0
  local rules_fired=""

  for sig in "$@"; do
    local rule=$(echo "$sig" | grep -o '"rule":"[^"]*"' | cut -d'"' -f4)
    local direction=$(echo "$sig" | grep -o '"direction":"[^"]*"' | cut -d'"' -f4)
    local strength=$(echo "$sig" | grep -o '"strength":"[^"]*"' | cut -d'"' -f4)
    [ -z "$rule" ] && continue
    rules_fired="${rules_fired},${rule}"

    # === 买入/看多形态（加分） ===
    case "$rule" in
      ma_convergence_up)     bs=0.15; desc="均线粘合-向上" ;;
      ma_convergence_down)   bs=0;    desc="均线粘合-观望" ;;

      # 经典K线形态
      red_three)             bs=0.30; desc="红三兵" ;;
      hammer)                bs=0.20; desc="锤子线-底部" ;;
      gap_up)                bs=0.20; desc="向上跳空" ;;

      # 均线趋势形态
      bullish_arrangement)   bs=0.50; desc="均线多头排列" ;;
      ma_golden_cross)       bs=0.30; desc="5日金叉20日" ;;

      # MACD形态
      macd_bottom_div)       bs=0.40; desc="MACD底背离" ;;
      macd_golden_cross)     bs=0.35; desc="MACD零轴金叉" ;;
      macd_above_zero)       bs=0.15; desc="MACD零轴上方" ;;
      macd_death_cross)      bs=-0.35; desc="MACD死叉" ;;
      macd_death_ongoing)     bs=-0.20; desc="MACD持续死叉" ;;

      # 突破形态
      breakout_up)           bs=0.35; desc="突破20日高点" ;;
      2b_fake_breakdown)     bs=0.50; desc="2B假跌破反转" ;;

      # 量价形态
      vol_up_with_price)     bs=0.25; desc="价涨量增-健康" ;;
      vol_down_shrink)       bs=0.20; desc="价跌量缩-洗盘" ;;
      volume_shrink)         bs=0.10; desc="缩量-洗盘特征" ;;

      # 逆势/超卖
      should_fall_strong)    bs=0.40; desc="逆势走强" ;;
      rsi_oversold)          bs=0.15; desc="RSI超卖" ;;

      # 板块+形态组合
      outperform_sector)     bs=0.20; desc="跑赢板块" ;;
      shrink_then_breakout)  bs=0.50; desc="缩量后放量突破" ;;

      # 🔥 缩量回踩均线支撑+主力未出走（辉哥指定高权重）
      volume_pullback_support) bs=0.50; desc="缩量回踩支撑+主力未出走" ;;
      # 🔥 缩量见底+放量反包（辉哥指定：永鼎案例）
      shrink_reversal)       bs=0.50; desc="缩量见底+放量反包" ;;

      # === K线卖出形态（减分） ===
      shooting_star)         bs=-0.50; desc="射击之星-高位反转" ;;
      upper_wick)            bs=-0.20; desc="倒锤线-长上影" ;;
      three_crows)           bs=-0.30; desc="三只乌鸦" ;;
      hanging_man)           bs=-0.25; desc="上吊线-高位" ;;
      gap_down)              bs=-0.25; desc="向下跳空" ;;

      # 前高压制/阻力
      historical_resistance) bs=-0.35; desc="前高阻力" ;;
      approach_resistance)   bs=-0.20; desc="接近前高" ;;
      historical_breakthrough) bs=0.30; desc="前高突破" ;;

      # 筹码分布信号
      chip_resistance)       bs=-0.40; desc="筹码套牢区-压制" ;;
      chip_density_low)      bs=0.30; desc="低位筹码密集" ;;
      chip_deviation_high)   bs=-0.30; desc="大幅偏离成本-获利盘" ;;
      chip_below_cost)       bs=0.20; desc="低于主力成本-超跌" ;;

      bearish_arrangement)   bs=-0.50; desc="均线空头排列" ;;
      ma_death_cross)        bs=-0.30; desc="5日死叉20日" ;;

      macd_top_div)          bs=-0.40; desc="MACD顶背离" ;;
      macd_below_zero)       bs=-0.15; desc="MACD零轴下方" ;;

      breakdown)             bs=-0.30; desc="跌破20日低点" ;;
      2b_fake_breakout)      bs=-0.50; desc="2B假突破反转" ;;

      vol_down_with_vol)     bs=-0.30; desc="价跌量增-出货" ;;
      vol_up_no_vol)         bs=-0.15; desc="价涨量缩-乏力" ;;
      volume_surge)          bs=-0.10; desc="巨量-天量有妖" ;;

      should_rise_fail)      bs=-0.40; desc="该涨不涨" ;;
      rsi_overbought)        bs=-0.25; desc="RSI超买" ;;
      limit_up)              bs=0;     desc="涨停板-中性信号" ;;
      limit_down)            bs=-0.30; desc="跌停板" ;;

      # 跑输板块
      underperform_sector)   bs=-0.15; desc="跑输板块" ;;

      # 中性/管理类规则不产生形态分
      *)                     bs=0    ;;
    esac

    if [ "$(echo "$bs > 0" | bc -l 2>/dev/null)" = "1" ]; then
      bull_score=$(echo "$bull_score + $bs" | bc -l)
      ((bull_count++))
    elif [ "$(echo "$bs < 0" | bc -l 2>/dev/null)" = "1" ]; then
      bear_score=$(echo "$bear_score + $bs" | bc -l)
      ((bear_count++))
    fi
  done

  # 综合分数 = 看多总分 - 看空总分绝对值
  local combined=$(echo "$bull_score + $bear_score" | bc -l 2>/dev/null)

  # 多形态共振加分：≥2个不同看多形态同时触发 → 共振加分
  if [ "$bull_count" -ge 2 ]; then
    multi_bonus=0.15
  fi
  if [ "$bull_count" -ge 3 ]; then
    multi_bonus=0.30
  fi

  # 高级共振：特定形态组合 = 经典买点 → 额外加分
  # 红三兵+放量突破 → 突破确认
  if echo "$rules_fired" | grep -q "red_three" && echo "$rules_fired" | grep -q "breakout_up"; then
    multi_bonus=$(echo "$multi_bonus + 0.20" | bc -l)
  fi
  # 多头排列+金叉 → 趋势确认
  if echo "$rules_fired" | grep -q "bullish_arrangement" && echo "$rules_fired" | grep -q "ma_golden_cross"; then
    multi_bonus=$(echo "$multi_bonus + 0.15" | bc -l)
  fi
  # MACD底背离+缩量洗盘 → 底部确认
  if echo "$rules_fired" | grep -q "macd_bottom_div" && echo "$rules_fired" | grep -q "vol_down_shrink"; then
    multi_bonus=$(echo "$multi_bonus + 0.25" | bc -l)
  fi
  # 逆势走强+突破 → 强突破
  if echo "$rules_fired" | grep -q "should_fall_strong" && echo "$rules_fired" | grep -q "breakout_up"; then
    multi_bonus=$(echo "$multi_bonus + 0.20" | bc -l)
  fi
  # 向上跳空+放量 → 真实突破
  if echo "$rules_fired" | grep -q "gap_up" && echo "$rules_fired" | grep -q "vol_up_with_price"; then
    multi_bonus=$(echo "$multi_bonus + 0.15" | bc -l)
  fi
  # 2B假跌破+锤子线 → 双底确认
  if echo "$rules_fired" | grep -q "2b_fake_breakdown" && echo "$rules_fired" | grep -q "hammer"; then
    multi_bonus=$(echo "$multi_bonus + 0.20" | bc -l)
  fi
  # 射击之星+RSI超买+换手率异常 → 多头力竭三重确认（辉哥2026-05-23杭电实战验证）
  if echo "$rules_fired" | grep -q "shooting_star" && echo "$rules_fired" | grep -q "rsi_overbought" && echo "$rules_fired" | grep -q "turnover_abnormal"; then
    multi_bonus=$(echo "$multi_bonus - 0.20" | bc -l)
  fi

  local total=$(echo "$combined + $multi_bonus" | bc -l 2>/dev/null)

  # 限幅 -1.0 ~ +1.0
  if [ "$(echo "$total > 1.0" | bc -l 2>/dev/null)" = "1" ]; then
    total=1.0
  elif [ "$(echo "$total < -1.0" | bc -l 2>/dev/null)" = "1" ]; then
    total=-1.0
  fi

  # 前导零修复
  total=$(echo "$total" | sed 's/^\./0./;s/^-\./-0./')
  echo "$total"
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
  local score_details=$(echo "$score_result" | cut -d'|' -f2-)
  [ -z "$quality_score" ] && quality_score=3.0
  # 前导零修复（bc输出.5而不是0.5）
  quality_score=$(echo "$quality_score" | sed 's/^\./0./;s/^-\./-0./')
  # P0v2：从signals[]中扫描已有规则的形态信号
  local morph_score=$(scan_morphology_signals "${signals[@]}")
  [ -z "$morph_score" ] && morph_score=0
  # 前导零修复
  morph_score=$(echo "$morph_score" | sed 's/^\./0./;s/^-\./-0./')
  # 形态因子不计入4分上限，额外加到总分
  local total_score_ext=$(echo "$quality_score + $morph_score" | bc -l 2>/dev/null)
  [ -z "$total_score_ext" ] && total_score_ext=$quality_score

  # 盘前缓冲：量能不可用时，用日线趋势替代评分
  if echo "$score_details" | grep -q '量能:0'; then
    local buffer_score=0.0
    if [ -n "$ma5" ] && [ -n "$ma10" ] && [ -n "$ma20" ] && [ "$(echo "$ma5 > $ma10" | bc -l 2>/dev/null)" = "1" ] && [ "$(echo "$ma10 > $ma20" | bc -l 2>/dev/null)" = "1" ]; then
      buffer_score=$(echo "$buffer_score + 0.5" | bc -l)
    fi
    if [ -n "$ma5" ] && [ -n "$price" ] && [ "$(echo "$price > $ma5" | bc -l 2>/dev/null)" = "1" ]; then
      buffer_score=$(echo "$buffer_score + 0.25" | bc -l)
    fi
    if [ -n "$dif" ] && [ "$(echo "$dif > 0" | bc -l 2>/dev/null)" = "1" ]; then
      buffer_score=$(echo "$buffer_score + 0.25" | bc -l)
    fi
    if [ "$(echo "$buffer_score > 0" | bc -l 2>/dev/null)" = "1" ]; then
      total_score_ext=$(echo "$total_score_ext + $buffer_score" | bc -l)
      score_details="${score_details}|缓:$buffer_score"
    fi
  fi

  total_score_ext=$(echo "$total_score_ext" | sed 's/^\./0./;s/^-\./-0./')

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
  STOCK_TURNOVER=$(echo "$d" | awk -F'~' '{print $39}' 2>/dev/null)
  VOL_RATIO_GLOBAL=$(echo "$d" | awk -F'~' '{print $50}' 2>/dev/null)
  [ -z "$STOCK_TURNOVER" ] && STOCK_TURNOVER=0
  [ -z "$VOL_RATIO_GLOBAL" ] && VOL_RATIO_GLOBAL=0
  # 内外盘（全局变量，规则函数可访问）
  OUTER_DISK=$(echo "$d" | awk -F'~' '{print $8}' 2>/dev/null)
  INNER_DISK=$(echo "$d" | awk -F'~' '{print $9}' 2>/dev/null)
  [ -z "$OUTER_DISK" ] && OUTER_DISK=0
  [ -z "$INNER_DISK" ] && INNER_DISK=0
  
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
