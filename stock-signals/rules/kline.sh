#!/bin/bash
# kline.sh — K线形态规则
# args: code name price change open high low yclose vol ma5 ma10 ma20 ma60 avg5v high20 low20 dif prev_dif prev_close mkt_sh mkt_cy

rule_hammer_hanging_man() {
  local price="$3" open="$5" high="$6" low="$7" ma10="${11}" name="$2"
  [ -z "$open" ] || [ -z "$low" ] || [ "$open" = "0.000" ] && return
  
  local body=$(echo "scale=2; $price - $open" | bc -l 2>/dev/null)
  local body_abs=$(echo "$body" | sed 's/^-//')
  local shadow_down=$(echo "scale=2; $price - $low" | bc -l 2>/dev/null)
  
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

rule_doji() {
  local price="$3" open="$5"
  [ -z "$open" ] || [ "$open" = "0.000" ] && return
  
  local dev=$(echo "scale=4; ($price-$open)/$open*100" | bc -l 2>/dev/null | sed 's/^-//')
  if [ "$(echo "$dev < 0.5" | bc -l 2>/dev/null)" = "1" ]; then
    echo "{\"rule\":\"doji\",\"direction\":\"reversal_warn\",\"strength\":\"low\",\"note\":\"十字星-多空均衡\"}"
  fi
}

rule_three_candles() {
  local price="$3" change="$4" code="$1"
  local cache="$SIGNAL_DIR/cache/${code}.day"
  [ ! -f "$cache" ] || [ "$(wc -l < "$cache")" -lt 3 ] && return
  
  local c3=$(tail -3 "$cache" | head -1 | awk '{print $1}')
  local c2=$(tail -2 "$cache" | head -1 | awk '{print $1}')
  local c1=$(tail -1 "$cache" | awk '{print $1}')
  [ -z "$c3" ] || [ -z "$c2" ] || [ -z "$c1" ] && return
  
  local d2=$(echo "scale=2; ($c2-$c3)/$c3*100" | bc -l 2>/dev/null)
  local d1=$(echo "scale=2; ($c1-$c2)/$c2*100" | bc -l 2>/dev/null)
  [ "$(echo "$d2 > 0" | bc -l 2>/dev/null)" != "1" ] && return
  
  # 红三兵 + 量能确认（至少1天成交量>10日均量）
  if [ "$(echo "$d2 > 0.5 && $d1 > 0.5 && $change > 0" | bc -l 2>/dev/null)" = "1" ]; then
    local v2=$(tail -2 "$cache" | head -1 | awk '{print $2}')
    local v1=$(tail -1 "$cache" | awk '{print $2}')
    local avgvol=$(tail -10 "$cache" | awk '{s+=$2} END{printf "%.0f", s/10}')
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

  # 前日数据 (cache格式: close vol open high low)
  local prev_data=($(tail -2 "$cache" | head -1))
  local prev_close="${prev_data[0]}" prev_vol="${prev_data[1]}"
  local prev_open="${prev_data[2]}" prev_high="${prev_data[3]}"

  local prev2_data=($(tail -3 "$cache" | head -1))
  local prev2_close="${prev2_data[0]}"

  [ -z "$prev_close" ] || [ -z "$prev_vol" ] || [ -z "$prev2_close" ] && return

  # 10日均量
  local avg_vol=$(tail -10 "$cache" | awk '{s+=$2} END{printf "%.0f", s/10}')
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

rule_gap_detection() {
  local open="$5" yclose="$8"
  [ -z "$open" ] || [ -z "$yclose" ] || [ "$open" = "0.000" ] || [ "$yclose" = "0.000" ] && return

  local gap_pct=$(echo "scale=2; ($open-$yclose)/$yclose*100" | bc -l 2>/dev/null)
  local abs_gap=$(echo "$gap_pct" | sed 's/^-//' 2>/dev/null)

  if [ "$(echo "$abs_gap > 1" | bc -l 2>/dev/null)" = "1" ]; then
    if [ "$(echo "$gap_pct > 0" | bc -l 2>/dev/null)" = "1" ]; then
      echo "{\"rule\":\"gap_up\",\"direction\":\"bullish\",\"gap_pct\":$gap_pct,\"strength\":\"medium\",\"note\":\"向上跳空+${gap_pct}%—突破或消息驱动\"}"
    else
      echo "{\"rule\":\"gap_down\",\"direction\":\"bearish\",\"gap_pct\":$gap_pct,\"strength\":\"high\",\"note\":\"向下跳空${gap_pct}%—需警惕\"}"
    fi
  fi
}
