#!/bin/bash
# kline.sh — K线形态规则
# args: code name price change open high low yclose vol ma5 ma10 ma20 ma60 avg5v high20 low20 dif prev_dif prev_close mkt_sh mkt_cy
# 所有函数可通过 load_kline_cache <code> 获取最近10根日K线

# ──────────────────────────────────────────────
# 工具：从价格缓存读取历史K线（最近N根）
# 返回 bash 数组声明，如 past_close[0..N-1]<newline>past_high[0..N-1]...
# 调用后可直接引用: past_close, past_high, past_low, past_open, past_vol
# ──────────────────────────────────────────────
_read_kline_history() {
  local code="$1" n="${2:-5}"
  local cache_dir="/root/.openclaw/workspace/stock-signals/cache"
  local found=""
  # 直接匹配无前缀缓存文件名（engine.sh写入格式为 ${code}.day）
  local p="$cache_dir/${code}.day"
  [ -f "$p" ] && found="$p"
  [ -z "$found" ] && return 1
  
  local lines=$(cat "$found" 2>/dev/null | tail -"$n")
  [ -z "$lines" ] && return 1
  
  local close_list="" open_list="" high_list="" low_list="" vol_list=""
  while IFS= read -r line; do
    [ -z "$line" ] && continue
    local p_c=$(echo "$line" | awk '{print $1}')
    [ -z "$p_c" ] && continue
    
    # 检查列数：旧格式(3列 close vol date) vs 新格式(6列 close open high low vol date)
    local nf=$(echo "$line" | awk '{print NF}')
    if [ "$nf" -ge 5 ]; then
      # 新格式 OHLCV
      local p_o=$(echo "$line" | awk '{print $2}')
      local p_h=$(echo "$line" | awk '{print $3}')
      local p_l=$(echo "$line" | awk '{print $4}')
      local p_v=$(echo "$line" | awk '{print $5}')
    else
      # 旧格式兼容
      local p_o="$p_c" p_h="$p_c" p_l="$p_c"
      local p_v=$(echo "$line" | awk '{print $2}')
    fi
    
    [ -n "$close_list" ] && close_list="$close_list "
    close_list="${close_list}${p_c}"
    [ -n "$open_list" ] && open_list="$open_list "
    open_list="${open_list}${p_o}"
    [ -n "$high_list" ] && high_list="$high_list "
    high_list="${high_list}${p_h}"
    [ -n "$low_list" ] && low_list="$low_list "
    low_list="${low_list}${p_l}"
    if [ -n "$vol_list" ]; then vol_list="$vol_list "; fi
    vol_list="${vol_list}${p_v}"
  done <<< "$lines"
  
  echo "PAST_CLOSE=($close_list)"
  echo "PAST_OPEN=($open_list)"
  echo "PAST_HIGH=($high_list)"
  echo "PAST_LOW=($low_list)"
  echo "PAST_VOL=($vol_list)"
  return 0
}

rule_hammer_hanging_man() {
  local price="$3" open="$5" high="$6" low="$7" ma10="${11}" name="$2"
  [ -z "$open" ] || [ -z "$low" ] || [ "$open" = "0.000" ] && return
  
  local body=$(echo "scale=2; $price - $open" | bc -l 2>/dev/null)
  local body_abs=$(echo "$body" | sed 's/^-//')
  # 下影线 = min(close, open) - low（实体下沿到最低价）
  local lower_end=$(echo "if($price < $open) $price else $open" | bc -l 2>/dev/null)
  local shadow_down=$(echo "scale=2; $lower_end - $low" | bc -l 2>/dev/null)
  
  [ "$(echo "$body_abs == 0" | bc -l 2>/dev/null)" = "1" ] && return
  [ "$(echo "$shadow_down >= $body_abs * 1.5" | bc -l 2>/dev/null)" != "1" ] && return
  
  if [ -n "$ma10" ]; then
    if [ "$(echo "$price < $ma10" | bc -l 2>/dev/null)" = "1" ]; then
      # 阳线确认：收盘>开盘（买入意愿意味着确认）
      if [ "$(echo "$price > $open" | bc -l 2>/dev/null)" = "1" ]; then
        echo "{\"rule\":\"hammer\",\"direction\":\"buy_signal\",\"strength\":\"medium\",\"note\":\"锤子线-底部阳线确认长下影\"}"
      fi
    elif [ "$(echo "$price > $ma10 * 1.1" | bc -l 2>/dev/null)" = "1" ]; then
      echo "{\"rule\":\"hanging_man\",\"direction\":\"sell_signal\",\"strength\":\"medium\",\"note\":\"上吊线-高位长下影\"}"
    fi
  fi
}

# ──────────────────────────────────────────────
# 十字星「变盘向上」识别（辉哥四把锁）
# 锁1：位置——十字星必须在关键支撑位
# 锁2：量能——缩量到抛压枯竭
# 锁3：内部结构——下影承接 vs 上影抛压
# 锁4：次日确认——后一根K线盖章（需调用方次日再跑）
# 输出两个信号：
#   doji_bullish_candidate — 锁1+2+3满足，变盘向上候选
#   doji_bullish_confirmed — 锁1+2+3+4满足，向上确认
# ──────────────────────────────────────────────
rule_doji() {
  local code="$1" price="$3" change="$4" open="$5" high="$6" low="$7" yclose="$8" vol="$9"
  local ma5="${10}" ma10="${11}" ma20="${12}" avg5v="${14}"
  [ -z "$open" ] || [ "$open" = "0.000" ] || [ -z "$low" ] || [ -z "$high" ] && return
  [ "$(echo "$high == $low" | bc -l 2>/dev/null)" = "1" ] && return
  
  # ── 定义十字星/小纺锤（实体/影线 < 0.15）──
  local body=$(echo "scale=4; $price - $open" | bc -l 2>/dev/null)
  local body_abs=$(echo "$body" | sed 's/^-//')
  local total_range=$(echo "scale=4; $high - $low" | bc -l 2>/dev/null)
  [ "$(echo "$total_range == 0" | bc -l 2>/dev/null)" = "1" ] && return
  
  local body_range_ratio=$(echo "scale=4; $body_abs / $total_range" | bc -l 2>/dev/null)
  # 条件放宽到0.15，覆盖小纺锤/螺旋桨（比纯十字更实用）
  [ "$(echo "$body_range_ratio >= 0.15" | bc -l 2>/dev/null)" = "1" ] && return
  
  # ── 绝对意义：实体/收盘价 < 0.015（防跳空扭曲，但比完美十字放宽）──
  local body_close_ratio=$(echo "scale=4; $body_abs / $price" | bc -l 2>/dev/null)
  [ "$(echo "$body_close_ratio >= 0.015" | bc -l 2>/dev/null)" = "1" ] && return
  
  # ── 🔑 锁3：内部结构（上下影力学）──
  local upper=$(echo "if($price > $open) $high - $price else $high - $open" | bc -l 2>/dev/null)
  local lower=$(echo "if($price < $open) $price - $low else $open - $low" | bc -l 2>/dev/null)
  local upper_abs=$(echo "$upper" | sed 's/^-//')
  local lower_abs=$(echo "$lower" | sed 's/^-//')
  
  # 下影明显（被买盘接住）vs 上影明显（抛压重）
  local is_dragonfly=0  # 锤子十字（下影>>上影）
  local is_gravestone=0 # 墓碑十字（上影>>下影）
  if [ "$(echo "$lower_abs > $upper_abs * 2.5" | bc -l 2>/dev/null)" = "1" ]; then
    is_dragonfly=1
  fi
  if [ "$(echo "$upper_abs > $lower_abs * 2.5" | bc -l 2>/dev/null)" = "1" ]; then
    is_gravestone=1
  fi
  
  # ── 🔑 锁1：位置——必须在关键支撑位 ──
  # 条件A：收盘在MA20附近（±1.5%）或MA10附近（±1%）
  local at_support=0
  local support_type=""
  if [ -n "$ma20" ] && [ "$(echo "$ma20 > 0" | bc -l 2>/dev/null)" = "1" ]; then
    local dev_ma20=$(echo "scale=4; ($price - $ma20) / $ma20 * 100" | bc -l 2>/dev/null | sed 's/^-//')
    if [ "$(echo "$dev_ma20 <= 1.5" | bc -l 2>/dev/null)" = "1" ]; then
      at_support=1
      support_type="MA20"
    fi
  fi
  if [ "$at_support" -eq 0 ] && [ -n "$ma10" ] && [ "$(echo "$ma10 > 0" | bc -l 2>/dev/null)" = "1" ]; then
    local dev_ma10=$(echo "scale=4; ($price - $ma10) / $ma10 * 100" | bc -l 2>/dev/null | sed 's/^-//')
    if [ "$(echo "$dev_ma10 <= 1.0" | bc -l 2>/dev/null)" = "1" ]; then
      at_support=1
      support_type="MA10"
    fi
  fi
  
  # ── 🔑 锁2：量能——缩量到抛压枯竭 ──
  # 量比 < 0.95 或 量 < MAV5×0.85
  local vol_shrink=0
  if [ -n "$avg5v" ] && [ "$(echo "$avg5v > 0" | bc -l 2>/dev/null)" = "1" ]; then
    local vol_ratio=$(echo "scale=4; $vol / $avg5v" | bc -l 2>/dev/null)
    if [ "$(echo "$vol_ratio < 0.95" | bc -l 2>/dev/null)" = "1" ]; then
      vol_shrink=1
    fi
  fi
  
  # ── 输出信号 ──
  local note=""
  local direction=""
  local strength=""
  
  if [ "$at_support" -eq 1 ] && [ "$vol_shrink" -eq 1 ] && [ "$is_gravestone" -eq 0 ]; then
    # ✅ 锁1+2+3：位置支撑 + 缩量 + 非墓碑 → 变盘向上候选
    local shape=""
    [ "$is_dragonfly" -eq 1 ] && shape="锤子十字" || shape="小纺锤"
    note="${shape}·${support_type}支撑·缩量${vol_ratio:-?}·抛压枯竭"
    direction="bullish_watch"
    strength="medium"
    echo "{\"rule\":\"doji_bullish_candidate\",\"direction\":\"$direction\",\"strength\":\"$strength\",\"note\":\"十字星-向上候选｜$note\"}"
  elif [ "$is_gravestone" -eq 1 ] && [ "$vol_shrink" -eq 0 ]; then
    # ❌ 墓碑十字+放量 → 变盘向下预警
    note="墓碑十字·上影极长·放量分歧"
    direction="sell_signal"
    strength="medium"
    echo "{\"rule\":\"doji_bearish_warn\",\"direction\":\"$direction\",\"strength\":\"$strength\",\"note\":\"十字星-向下预警｜$note\"}"
  else
    # 普通十字星，无明确方向
    note="多空均衡·方向待次日确认"
    direction="neutral"
    strength="low"
    echo "{\"rule\":\"doji\",\"direction\":\"$direction\",\"strength\":\"$strength\",\"note\":\"十字星-中性｜$note\"}"
  fi
}

# ──────────────────────────────────────────────
# 十字星向上确认（次日调用）
# 锁4：次日阳线收盘 > 十字星高点 + 放量
# 调用时机：次日运行engine.sh时，通过缓存读取前一日K线
# ──────────────────────────────────────────────
rule_doji_confirmed() {
  local code="$1" price="$3" change="$4" open="$5" high="$6" vol="$9"
  
  # 读取前一日K线（缓存中倒数第2条）
  local cache="$SIGNAL_DIR/cache/${code}.day"
  [ ! -f "$cache" ] && return
  local nlines=$(wc -l < "$cache" 2>/dev/null | tr -d ' ')
  [ -z "$nlines" ] || [ "$nlines" -lt 2 ] && return
  
  local y_line=$(tail -2 "$cache" | head -1 2>/dev/null)
  [ -z "$y_line" ] && return
  
  local y_close=$(echo "$y_line" | awk '{print $1}')
  local y_open=$(echo "$y_line" | awk '{print $2}')
  local y_high=$(echo "$y_line" | awk '{print $3}')
  local y_low=$(echo "$y_line" | awk '{print $4}')
  local y_vol=$(echo "$y_line" | awk '{print $5}')
  [ -z "$y_close" ] || [ -z "$y_high" ] && return
  
  # 检查前一日是否为十字星候选
  local y_body=$(echo "scale=4; $y_close - $y_open" | bc -l 2>/dev/null)
  local y_body_abs=$(echo "$y_body" | sed 's/^-//')
  local y_range=$(echo "scale=4; $y_high - $y_low" | bc -l 2>/dev/null)
  [ "$(echo "$y_range == 0" | bc -l 2>/dev/null)" = "1" ] && return
  
  local y_body_ratio=$(echo "scale=4; $y_body_abs / $y_range" | bc -l 2>/dev/null)
  [ "$(echo "$y_body_ratio >= 0.15" | bc -l 2>/dev/null)" = "1" ] && return
  
  # 前一日是十字星 → 检查今日确认
  # 条件A：今日收盘 > 前日最高价（阳线反包十字星高点）
  [ "$(echo "$price <= $y_high" | bc -l 2>/dev/null)" = "1" ] && return
  
  # 条件B：今日放量（量能 > 前日量能 × 1.15）
  local y_vol_num=$(echo "$y_vol" | sed 's/\..*//' 2>/dev/null)
  [ -z "$y_vol_num" ] && return
  [ "$(echo "$vol > $y_vol_num * 1.15" | bc -l 2>/dev/null)" != "1" ] && return
  
  # 条件C：今日涨幅 > 0
  [ "$(echo "$change > 0" | bc -l 2>/dev/null)" != "1" ] && return
  
  echo "{\"rule\":\"doji_bullish_confirmed\",\"direction\":\"buy_signal\",\"strength\":\"high\",\"note\":\"十字星-向上确认｜昨十字星+今阳线过昨高·放量${vol}·涨${change}%\"}"
}

rule_three_candles() {
  local price="$3" change="$4" code="$1" open="$5" vol="$9"
  local cache="$SIGNAL_DIR/cache/${code}.day"
  [ ! -f "$cache" ] || [ "$(wc -l < "$cache")" -lt 3 ] && return
  
  # 缓存最新两条是D-2和D-1（昨日），今日D0用实时参数
  local c3=$(tail -2 "$cache" | head -1 | awk '{print $1}')  # D-2
  local c2=$(tail -1 "$cache" | awk '{print $1}')             # D-1（昨日）
  local c1="$price"                                            # D0（今日实时）
  [ -z "$c3" ] || [ -z "$c2" ] && return
  
  local d2=$(echo "scale=2; ($c2-$c3)/$c3*100" | bc -l 2>/dev/null)  # D-1 vs D-2
  local d1=$(echo "scale=2; ($c1-$c2)/$c2*100" | bc -l 2>/dev/null)  # D0 vs D-1
  [ "$(echo "$d2 > 0" | bc -l 2>/dev/null)" != "1" ] && return
  
  # 红三兵 + 量能确认（至少1天成交量>10日均量）
  if [ "$(echo "$d2 > 0.5 && $d1 > 0.5 && $change > 0" | bc -l 2>/dev/null)" = "1" ]; then
    local v2=$(tail -1 "$cache" | awk '{print $5}')  # D-1量
    local v1="$vol"                                   # D0量（今日实时）
    local avgvol=$(tail -10 "$cache" | awk '{s+=$5} END{printf "%.0f", s/10}')
    local vol_ok=0
    [ -n "$v2" ] && [ -n "$avgvol" ] && [ "$v2" -gt "$avgvol" ] 2>/dev/null && ((vol_ok++))
    [ -n "$v1" ] && [ -n "$avgvol" ] && [ "$v1" -gt "$avgvol" ] 2>/dev/null && ((vol_ok++))
    if [ "$vol_ok" -ge 1 ]; then
      echo "{\"rule\":\"red_three\",\"direction\":\"bullish\",\"strength\":\"medium\",\"note\":\"红三兵-连续3日上涨+量能确认\"}"
    fi
  fi

  # 三只乌鸦
  if [ "${change:0:1}" = "-" ]; then
    if [ "$(echo "$d2 < -0.5 && $d1 < -0.5" | bc -l 2>/dev/null)" = "1" ]; then
      echo "{\"rule\":\"three_crows\",\"direction\":\"bearish\",\"strength\":\"high\",\"note\":\"三只乌鸦-连续3日下跌\"}"
    fi
  fi
}

rule_shooting_star() {
  local price="$3" open="$5" high="$6" low="$7" high20="${15}" ma20="${12}"
  [ -z "$open" ] || [ -z "$high" ] || [ "$open" = "0.000" ] && return

  local body=$(echo "scale=2; $price - $open" | bc -l 2>/dev/null)
  local body_abs=$(echo "$body" | sed 's/^-//')
  [ "$(echo "$body_abs == 0" | bc -l 2>/dev/null)" = "1" ] && return
  # 实体很小才可能是射击之星/倒锤线
  [ "$(echo "$body_abs < $price * 0.02" | bc -l 2>/dev/null)" != "1" ] && return

  # 上影线 = high - max(close, open)
  local upper_end=$(echo "if($price > $open) $price else $open" | bc -l 2>/dev/null)
  local upper_shadow=$(echo "scale=2; $high - $upper_end" | bc -l 2>/dev/null)
  [ "$(echo "$upper_shadow <= 0" | bc -l 2>/dev/null)" = "1" ] && return

  # 下影线 = min(close, open) - low
  local lower_end=$(echo "if($price < $open) $price else $open" | bc -l 2>/dev/null)
  local lower_shadow=$(echo "scale=2; $lower_end - $low" | bc -l 2>/dev/null)

  # 上影线至少是实体的2倍
  [ "$(echo "$upper_shadow < $body_abs * 2" | bc -l 2>/dev/null)" = "1" ] && return
  # 上影线长于下影线
  [ "$(echo "$upper_shadow <= $lower_shadow" | bc -l 2>/dev/null)" = "1" ] && return

  local up_ratio=$(echo "scale=1; $upper_shadow / $body_abs" | bc -l 2>/dev/null)

  # 判定位置：靠近20日高点 或 远离MA20超过5%
  local at_high=0
  if [ -n "$high20" ] && [ "$(echo "$price >= $high20 * 0.95" | bc -l 2>/dev/null)" = "1" ]; then
    at_high=1
  fi
  if [ -n "$ma20" ] && [ "$(echo "($price - $ma20) / $ma20 * 100 > 5" | bc -l 2>/dev/null)" = "1" ]; then
    at_high=1
  fi

  if [ "$at_high" -eq 1 ]; then
    echo "{\"rule\":\"shooting_star\",\"direction\":\"bearish\",\"strength\":\"very_high\",\"note\":\"射击之星-高位长上影,上影${upper_shadow}/实体${body_abs}=${up_ratio}x\"}"
  else
    echo "{\"rule\":\"upper_wick\",\"direction\":\"bearish_warn\",\"strength\":\"medium\",\"note\":\"倒锤线-长上影${upper_shadow}/实体${body_abs}=${up_ratio}x\"}"
  fi
}

# 缩量回踩关键均线/支撑位 + 主力资金未大幅流出
# 经典洗盘确认买点：缩量+回踩支撑+内外盘健康
# 使用全局变量：VOL_RATIO_GLOBAL(量比), OUTER_DISK(外盘), INNER_DISK(内盘)
# ──────────────────────────────────────────────
# 早晨之星（Morning Star）— 底部反转最强信号
# 三根K线：长阴→星线→长阳
# ──────────────────────────────────────────────
rule_morning_star() {
  local code="$1" price="$3" open="$5" high="$6" low="$7"
  
  # 加载历史K线（最近6根：D-2/D-1各1根 + D0量比需近5日均量）
  eval "$(_read_kline_history "$code" 6)" 2>/dev/null || return
  local n=${#PAST_CLOSE[@]}
  [ "$n" -lt 3 ] && return
  
  # ── D-2（index n-3）：大阴线，体现恐慌供给 ──
  local d2_close="${PAST_CLOSE[$((n-3))]}"
  local d2_open="${PAST_OPEN[$((n-3))]}"
  local d2_high="${PAST_HIGH[$((n-3))]}"
  local d2_low="${PAST_LOW[$((n-3))]}"
  [ -z "$d2_close" ] || [ -z "$d2_open" ] && return
  
  local d2_body=$(echo "scale=4; $d2_close - $d2_open" | bc -l 2>/dev/null)
  local d2_body_abs=$(echo "$d2_body" | sed 's/^-//')
  # 必须阴线，跌幅≥1.5%
  [ "$(echo "$d2_body >= 0" | bc -l 2>/dev/null)" = "1" ] && return
  [ "$(echo "$d2_body_abs < $d2_open * 0.015" | bc -l 2>/dev/null)" = "1" ] && return
  
  local d2_range=$(echo "scale=2; $d2_high - $d2_low" | bc -l 2>/dev/null)
  
  # ── D-1（index n-2）：小实体星线，抛压枯竭 ──
  local d1_close="${PAST_CLOSE[$((n-2))]}"
  local d1_open="${PAST_OPEN[$((n-2))]}"
  local d1_high="${PAST_HIGH[$((n-2))]}"
  local d1_low="${PAST_LOW[$((n-2))]}"
  [ -z "$d1_close" ] || [ -z "$d1_open" ] && return
  
  local d1_body=$(echo "scale=4; $d1_close - $d1_open" | bc -l 2>/dev/null)
  local d1_body_abs=$(echo "$d1_body" | sed 's/^-//')
  local d1_range=$(echo "scale=2; $d1_high - $d1_low" | bc -l 2>/dev/null)
  
  # 条件A: 实体相对D-2显著缩小（<0.4倍）
  local cond_rel=$(echo "$d1_body_abs < $d2_body_abs * 0.4" | bc -l 2>/dev/null)
  # 条件B: 实体绝对幅度很小（<3%），防小阳反弹混入
  local cond_abs=$(echo "$d1_body_abs < $d1_open * 0.03" | bc -l 2>/dev/null)
  # 条件C: 振幅收敛（D-1振幅 < D-2振幅 × 0.8）
  local cond_range=$(echo "$d1_range < $d2_range * 0.8" | bc -l 2>/dev/null)
  
  [ "$cond_rel" != "1" ] && return
  [ "$cond_abs" != "1" ] && return
  [ "$cond_range" != "1" ] && return
  
  # ── D0（今天，实时数据）：强势反包阳线 ──
  local d0_body=$(echo "scale=4; $price - $open" | bc -l 2>/dev/null)
  local d0_body_abs=$(echo "$d0_body" | sed 's/^-//')
  # 必须阳线，实体≥1.5%
  [ "$(echo "$d0_body <= 0" | bc -l 2>/dev/null)" = "1" ] && return
  [ "$(echo "$d0_body_abs < $price * 0.015" | bc -l 2>/dev/null)" = "1" ] && return
  
  # 收盘价必须高于D-2开盘价（强势反包）或至少高于D-2实体中点
  local d2_mid=$(echo "scale=2; ($d2_open + $d2_close) / 2" | bc -l 2>/dev/null)
  local cond_recover=$(echo "$price > $d2_open || $price > $d2_mid" | bc -l 2>/dev/null)
  [ "$cond_recover" != "1" ] && return
  
  # 量比确认：D0成交量 > 近5日均量 × 1.2
  local vol_sum=0 vol_cnt=0
  for ((i=n-5; i<n-1; i++)); do
    [ "$i" -lt 0 ] && continue
    local v="${PAST_VOL[$i]}"
    [ -z "$v" ] && continue
    vol_sum=$(echo "scale=0; $vol_sum + $v" | bc -l 2>/dev/null)
    vol_cnt=$((vol_cnt+1))
  done
  if [ "$vol_cnt" -gt 0 ]; then
    local avg_vol=$(echo "scale=0; $vol_sum / $vol_cnt" | bc -l 2>/dev/null)
    local t_vol="${PAST_VOL[$((n-1))]}"
    [ -z "$t_vol" ] && t_vol=0
    [ "$(echo "$t_vol < $avg_vol * 1.2" | bc -l 2>/dev/null)" = "1" ] && return
  fi
  
  local recovery=$(echo "scale=2; ($price - $d2_close) / $d2_close * 100" | bc -l 2>/dev/null)
  echo "{\"rule\":\"morning_star\",\"direction\":\"bullish\",\"strength\":\"very_high\",\"note\":\"早晨之星-底部反转|D-2阴${d2_body_abs}|D-1星${d1_body_abs}|D0阳${d0_body_abs}|回收${recovery}%\"}"
}

rule_volume_pullback_support() {
  local price="$3" ma5="${10}" ma10="${11}" ma20="${12}" low20="${16}" code="$1"

  # 条件1：缩量（量比<0.7）
  local vol_ratio="${VOL_RATIO_GLOBAL:-0}"
  [ "$(echo "$vol_ratio == 0" | bc -l 2>/dev/null)" = "1" ] && return
  [ "$(echo "$vol_ratio >= 0.7" | bc -l 2>/dev/null)" = "1" ] && return

  # 条件2：回踩到某条均线附近（±3%以内）
  local at_support=0; local support_type=""; local vol_ratio_pct=$(echo "scale=0; $vol_ratio * 100" | bc -l 2>/dev/null)
  [ "$vol_ratio_pct" -ge 70 ] 2>/dev/null && vol_ratio_pct=70

  # 检查MA5/MA10/MA20
  for pair in "ma5|$ma5" "ma10|$ma10" "ma20|$ma20"; do
    local ma_name="${pair%%|*}"; local ma_val="${pair##*|}"
    [ -z "$ma_val" ] || [ "$ma_val" = "0.000" ] || [ "$ma_val" = "n/a" ] && continue
    local dev=$(echo "scale=4; ($price - $ma_val) / $ma_val * 100" | bc -l 2>/dev/null)
    local dev_abs=$(echo "$dev" | sed 's/^-//' 2>/dev/null)
    if [ "$(echo "$dev_abs <= 3" | bc -l 2>/dev/null)" = "1" ]; then
      [ -n "$support_type" ] && support_type="${support_type}/"
      support_type="${support_type}${ma_name}(${ma_val})"
      at_support=1
    fi
  done

  # 检查20日低点附近（仅当均线未命中时）
  if [ "$at_support" -eq 0 ] && [ -n "$low20" ] && [ "$low20" != "0.000" ]; then
    local dev_low=$(echo "scale=4; ($price - $low20) / $low20 * 100" | bc -l 2>/dev/null)
    local dev_low_abs=$(echo "$dev_low" | sed 's/^-//' 2>/dev/null)
    [ "$(echo "$dev_low_abs <= 3" | bc -l 2>/dev/null)" = "1" ] && { support_type="20日低点($low20)"; at_support=1; }
  fi
  [ "$at_support" -eq 0 ] && return

  # 条件3：主力资金未大幅流出（外盘/内盘比>0.65）
  local outer="${OUTER_DISK:-0}"; local inner="${INNER_DISK:-0}"; local disk_ratio=0
  [ "$(echo "$inner > 0" | bc -l 2>/dev/null)" = "1" ] && disk_ratio=$(echo "scale=2; $outer / $inner" | bc -l 2>/dev/null)

  local disk_note=""
  if [ "$(echo "$disk_ratio >= 0.9" | bc -l 2>/dev/null)" = "1" ]; then
    disk_note="外盘/内盘=${disk_ratio}—主力资金稳健"; disk_ok=1
  elif [ "$(echo "$disk_ratio >= 0.65" | bc -l 2>/dev/null)" = "1" ]; then
    disk_note="外盘/内盘=${disk_ratio}—主力未大幅流出"; disk_ok=1
  else
    # 极度缩量（量比<0.4）时内外盘比差也可接受——空方力量已衰竭
    if [ "$(echo "$vol_ratio < 0.4" | bc -l 2>/dev/null)" = "1" ]; then
      disk_note="外盘/内盘=${disk_ratio}—极度缩量空方衰竭"; disk_ok=1
    else
      disk_ok=0
    fi
  fi

  [ "$disk_ok" -eq 1 ] || return

  local sig_strength="high"
  local support_lines=$(echo "$support_type" | grep -o "MA" | wc -l 2>/dev/null)
  [ "$support_lines" -ge 2 ] && sig_strength="very_high"

  echo "{\"rule\":\"volume_pullback_support\",\"direction\":\"buy_signal\",\"strength\":\"$sig_strength\",\"note\":\"缩量${vol_ratio_pct}%回踩${support_type}，${disk_note}\"}"
}

# 缩量见底+放量反包组合形态
# 经典底部反转模式：前日缩量下跌→今日放量阳线反包前日最高
rule_shrink_reversal() {
  local code="$1" price="$3" change="$4" vol="$9"
  local open="$5" high="$6"
  local cache="$SIGNAL_DIR/cache/${code}.day"
  [ ! -f "$cache" ] || [ "$(wc -l < "$cache")" -lt 3 ] && return

  # 前日数据 (cache格式: close open high low vol date)
  local prev_data=($(tail -2 "$cache" | head -1))
  local prev_close="${prev_data[0]}" prev_vol="${prev_data[4]}"
  local prev_open="${prev_data[1]}" prev_high="${prev_data[2]}"

  local prev2_data=($(tail -3 "$cache" | head -1))
  local prev2_close="${prev2_data[0]}"

  [ -z "$prev_close" ] || [ -z "$prev_vol" ] || [ -z "$prev2_close" ] && return

  # 10日均量
  local avg_vol=$(tail -10 "$cache" | awk '{s+=$5} END{printf "%.0f", s/10}')
  [ -z "$avg_vol" ] || [ "$avg_vol" = "0" ] && return

  # 条件1：前日缩量（量<均量的70%）
  local prev_ratio=$(echo "scale=2; $prev_vol / $avg_vol" | bc -l 2>/dev/null)
  [ "$(echo "$prev_ratio < 0.7" | bc -l 2>/dev/null)" != "1" ] && return

  # 条件2：前日或近日处于下跌/调整状态（检查前3日趋势）
  local prev_chg=$(echo "scale=2; ($prev_close - $prev2_close) / $prev2_close * 100" | bc -l 2>/dev/null)
  # 不严格要求前日一定跌，但前3日整体应偏弱（至少前日不是大涨）
  [ "$(echo "$prev_chg > 2" | bc -l 2>/dev/null)" = "1" ] && return

  # 条件3：本日放量（量比>1.2）
  local vol_ratio="${VOL_RATIO_GLOBAL:-0}"
  [ "$(echo "$vol_ratio > 1.2" | bc -l 2>/dev/null)" != "1" ] && return

  # 条件4：本日上涨且涨幅>2%
  [ "${change:0:1}" = "-" ] && return
  local abs_chg=$(echo "$change" | tr -d '%' | sed 's/^-//')
  [ "$(echo "$abs_chg < 2" | bc -l 2>/dev/null)" = "1" ] && return

  # 条件5：本日阳线反包前日最高价（收盘>前日最高）
  [ "$(echo "$price > $prev_high" | bc -l 2>/dev/null)" != "1" ] && return

  # 全部条件满足！
  local prev_ratio_pct=$(echo "scale=0; $prev_ratio * 100" | bc -l 2>/dev/null)
  local vol_ratio_pct=$(echo "scale=0; $vol_ratio * 100" | bc -l 2>/dev/null)
  local note="缩量见底+放量反包：前日缩量${prev_ratio_pct}%(较均量)跌${prev_chg}%，今日放量${vol_ratio_pct}x收${price}>前高${prev_high}"

  echo "{\"rule\":\"shrink_reversal\",\"direction\":\"buy_signal\",\"strength\":\"very_high\",\"note\":\"$note\"}"
}

# ──────────────────────────────────────────────
# 仙人指路（Fairy Guide）— 中继看涨
# 上升途中出现长上影K线（试盘），次日不跌反涨确认
# 拆分为两个独立规则：
#   fairy_guide_confirmed — 已触发（昨日形态+今日确认）
#   fairy_guide_forming   — 即将触发（昨日形态成立，等今日确认）
# 注意：缓存数组 PAST_* 的排列为 [0]=最旧 ...[n-1]=最新
#       因此昨日数据索引为 n-1（最新一条就是昨日收盘缓存）
# ──────────────────────────────────────────────

# 仙人指路-共同检查函数（抽离形态判定逻辑）
# 返回值：0=形态成立(并设置变量), 1=不成立
_fairy_guide_check_setup() {
  local code="$1"
  # 读取近6日K线（至少需要5日量能窗口+1日形态）
  eval "$(_read_kline_history "$code" 6)" 2>/dev/null || return 1
  local n=${#PAST_CLOSE[@]}
  [ "$n" -lt 3 ] && return 1
  
  # 昨日数据 = 最新一条缓存（索引 n-1）
  y_close="${PAST_CLOSE[$((n-1))]}"
  y_open="${PAST_OPEN[$((n-1))]}"
  y_high="${PAST_HIGH[$((n-1))]}"
  y_low="${PAST_LOW[$((n-1))]}"
  y_vol="${PAST_VOL[$((n-1))]}"
  [ -z "$y_close" ] || [ -z "$y_open" ] || [ -z "$y_high" ] || [ -z "$y_low" ] && return 1
  
  # 实体
  local yb=$(echo "scale=2; $y_close - $y_open" | bc -l 2>/dev/null)
  y_body_abs=$(echo "$yb" | sed 's/^-//')
  [ "$(echo "$y_body_abs == 0" | bc -l 2>/dev/null)" = "1" ] && return 1
  
  # 总振幅
  local total_range=$(echo "scale=2; $y_high - $y_low" | bc -l 2>/dev/null)
  [ "$(echo "$total_range == 0" | bc -l 2>/dev/null)" = "1" ] && return 1
  
  # ── 条件1：收盘在K线顶部1/4（位置法，对跳空不敏感）──
  # close_position = (close - low) / (high - low)，>0.75 表示收盘在顶部25%以内
  close_position=$(echo "scale=4; ($y_close - $y_low) / $total_range" | bc -l 2>/dev/null)
  [ "$(echo "$close_position <= 0.75" | bc -l 2>/dev/null)" = "1" ] && return 1
  
  # 辅助：上影线 ≥ 实体 × 1.5（保留传统条件，从2降到1.5因为位置法已做主判）
  local ue=$(echo "if($y_close > $y_open) $y_close else $y_open" | bc -l 2>/dev/null)
  y_upper_shadow=$(echo "scale=2; $y_high - $ue" | bc -l 2>/dev/null)
  up_ratio=$(echo "scale=1; $y_upper_shadow / $y_body_abs" | bc -l 2>/dev/null)
  [ "$(echo "$up_ratio < 1.5" | bc -l 2>/dev/null)" = "1" ] && return 1
  
  # ── 条件2：小实体（实体/振幅 < 0.3）──
  local body_range_ratio=$(echo "scale=4; $y_body_abs / $total_range" | bc -l 2>/dev/null)
  [ "$(echo "$body_range_ratio >= 0.3" | bc -l 2>/dev/null)" = "1" ] && return 1
  
  # ── 条件3：下影线很短（< 实体 × 0.5）──
  local le=$(echo "if($y_close < $y_open) $y_close else $y_open" | bc -l 2>/dev/null)
  y_lower_shadow=$(echo "scale=2; $le - $y_low" | bc -l 2>/dev/null)
  low_ratio=$(echo "scale=1; $y_lower_shadow / $y_body_abs" | bc -l 2>/dev/null)
  [ "$(echo "$low_ratio > 0.5" | bc -l 2>/dev/null)" = "1" ] && return 1
  
  # ── 条件4：股价在MA20之上（上升趋势）──
  local sum=0 cnt=0
  for ((i=0; i<n && cnt<20; i++)); do
    sum=$(echo "scale=2; $sum + ${PAST_CLOSE[$i]}" | bc -l 2>/dev/null)
    cnt=$((cnt+1))
  done
  [ "$cnt" -lt 5 ] && return 1
  ma20=$(echo "scale=2; $sum / $cnt" | bc -l 2>/dev/null)
  [ "$(echo "$y_close <= $ma20" | bc -l 2>/dev/null)" = "1" ] && return 1
  
  # ── 条件5：昨日量非近5日最大量（非天量出货）──
  local max5v=0
  for ((i=n-5; i<n; i++)); do
    [ "$i" -lt 0 ] && continue
    local v="${PAST_VOL[$i]}"
    [ -z "$v" ] && continue
    [ "$(echo "$v > $max5v" | bc -l 2>/dev/null)" = "1" ] && max5v="$v"
  done
  [ "$(echo "$y_vol >= $max5v * 0.95" | bc -l 2>/dev/null)" = "1" ] && return 1
  
  # 今日量（缓存的最后一条）
  t_vol="${PAST_VOL[$((n-1))]}"
  [ -z "$t_vol" ] && t_vol=0
  
  return 0
}

# 仙人指路-已触发（昨日长上影+今日确认上涨）
rule_fairy_guide_confirmed() {
  local code="$1" price="$3" change="$4" open="$5" high="$6" low="$7"
  
  _fairy_guide_check_setup "$code" || return
  
  # 条件C：今日股价在MA20之上
  [ "$(echo "$price < $ma20" | bc -l 2>/dev/null)" = "1" ] && return
  
  # 条件D：今日涨幅≥1%
  [ "$(echo "$change < 1" | bc -l 2>/dev/null)" = "1" ] && return
  
  # 条件E：今日高开确认（开盘 > 昨日收盘，说明在长上影区间内继续试探）
  [ "$(echo "$open <= $y_close" | bc -l 2>/dev/null)" = "1" ] && return
  
  # 条件F：今日量能 ≥ 昨日量能 × 0.7（温和放量，不缩量）
  # 注意：$9=今日vol参数，t_vol是缓存最新一条（昨日），这里用今日量
  local today_vol="${9}"
  [ "$(echo "$today_vol < $y_vol * 0.7" | bc -l 2>/dev/null)" = "1" ] && return
  
  # 加分项：今日是否突破昨日最高价
  local broke_high="否"
  [ "$(echo "$price > $y_high" | bc -l 2>/dev/null)" = "1" ] && broke_high="是"
  
  echo "{\"rule\":\"fairy_guide_confirmed\",\"direction\":\"bullish\",\"strength\":\"high\",\"note\":\"仙人指路-已触发｜昨收盘位${close_position}·实体/振幅=${body_range_ratio}·价在MA20上·昨量非天量→今涨${change}%·突破昨高=${broke_high}\"}"
}

# 仙人指路-即将触发（昨日形态成立，等待今日确认）
rule_fairy_guide_forming() {
  local code="$1" price="$3" change="$4" open="$5" high="$6" low="$7"
  
  _fairy_guide_check_setup "$code" || return
  
  # 条件C：今日股价在MA20之上
  [ "$(echo "$price < $ma20" | bc -l 2>/dev/null)" = "1" ] && return
  
  # 如果今日已满足confirmed条件，forming不重复输出
  [ "$(echo "$change >= 1" | bc -l 2>/dev/null)" = "1" ] && [ "$(echo "$open > $y_close" | bc -l 2>/dev/null)" = "1" ] && return
  
  # 输出即将触发
  local y_body_type="阳线"
  [ "$(echo "$y_close < $y_open" | bc -l 2>/dev/null)" = "1" ] && y_body_type="阴线"
  
  echo "{\"rule\":\"fairy_guide_forming\",\"direction\":\"bullish_watch\",\"strength\":\"medium\",\"note\":\"仙人指路-即将触发｜昨${y_body_type}·收盘位${close_position}·实体/振幅=${body_range_ratio}·昨量非天量→关注今日能否高开上涨确认\"}"
}

rule_gap_detection() {
  local open="$5" yclose="$8" price="$3" high="$6"
  [ -z "$open" ] || [ -z "$yclose" ] || [ "$open" = "0.000" ] || [ "$yclose" = "0.000" ] && return

  local gap_pct=$(echo "scale=2; ($open-$yclose)/$yclose*100" | bc -l 2>/dev/null)
  local abs_gap=$(echo "$gap_pct" | sed 's/^-//' 2>/dev/null)

  if [ "$(echo "$abs_gap > 1" | bc -l 2>/dev/null)" = "1" ]; then
    if [ "$(echo "$gap_pct > 0" | bc -l 2>/dev/null)" = "1" ]; then
      # 高开跳空——检查是否高开低走（从开盘价回落，非从最高点）
      local decline_from_open=$(echo "scale=2; ($open - $price) / $open * 100" | bc -l 2>/dev/null 2>/dev/null)
      if [ "$(echo "$gap_pct >= 5 && $price < $open && $decline_from_open >= 5" | bc -l 2>/dev/null 2>/dev/null)" = "1" ]; then
        # 高开5%以上 + 从开盘价回落超5% → 假突破式高开
        echo "{\"rule\":\"gap_up_meltdown\",\"direction\":\"bearish_warn\",\"gap_pct\":$gap_pct,\"strength\":\"very_high\",\"note\":\"跳空高开+${gap_pct}%但收盘回落${decline_from_open}%,缺口全吞-假突破\"}"
      else
        echo "{\"rule\":\"gap_up\",\"direction\":\"bullish\",\"gap_pct\":$gap_pct,\"strength\":\"medium\",\"note\":\"向上跳空+${gap_pct}%—突破或消息驱动\"}"
      fi
    else
      echo "{\"rule\":\"gap_down\",\"direction\":\"bearish\",\"gap_pct\":$gap_pct,\"strength\":\"high\",\"note\":\"向下跳空${gap_pct}%—需警惕\"}"
    fi
  fi
}
