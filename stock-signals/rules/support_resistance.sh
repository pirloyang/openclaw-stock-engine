#!/bin/bash
# support_resistance.sh — 突破/2B法则/筹码密集区/前高阻力（纯计算，用缓存数据）
# args: code name price change open high low yclose vol ma5 ma10 ma20 ma60 avg5v high20 low20 dif prev_dif prev_close mkt_sh mkt_cy


rule_breakout() {
  local code="$1" price="$3" high20="${15}" low20="${16}"
  [ -z "$high20" ] || [ -z "$low20" ] && return
  
  # 从cache读取最近60日的真实最高价作为突破参照
  local cache="$SIGNAL_DIR/cache/${code}.day"
  local high60="$high20"
  if [ -f "$cache" ] && [ "$(wc -l < "$cache")" -ge 5 ]; then
    local cache_high60=$(sort -k3 -t' ' "$cache" | tail -60 | awk '{if(max=="") max=$1; if($1>max) max=$1} END{print max}')
    [ -n "$cache_high60" ] && [ "$(echo "$cache_high60 > $high20" | bc -l 2>/dev/null)" = "1" ] && high60=$cache_high60
  fi

  if [ "$(echo "$price > $high60" | bc -l 2>/dev/null)" = "1" ]; then
    local breach=$(echo "scale=2; ($price-$high60)/$high60*100" | bc -l 2>/dev/null)
    echo "{\"rule\":\"breakout_up\",\"direction\":\"breakout\",\"strength\":\"high\",\"note\":\"突破${high60}.00高点+${breach}%\"}"
  elif [ "$(echo "$price < $low20" | bc -l 2>/dev/null)" = "1" ]; then
    local breach=$(echo "scale=2; ($low20-$price)/$low20*100" | bc -l 2>/dev/null)
    echo "{\"rule\":\"breakdown\",\"direction\":\"breakdown\",\"strength\":\"high\",\"note\":\"跌破20日低点-${breach}%\"}"
  fi
}

rule_2b() {
  local price="$3" high20="${15}" low20="${16}" prev_close="${19}"
  [ -z "$high20" ] || [ -z "$low20" ] || [ -z "$prev_close" ] && return
  
  if [ "$(echo "$prev_close > $high20 && $price < $high20" | bc -l 2>/dev/null)" = "1" ]; then
    local breach=$(echo "scale=2; ($prev_close-$high20)/$high20*100" | bc -l 2>/dev/null)
    if [ "$(echo "$breach < 3" | bc -l 2>/dev/null)" = "1" ]; then
      echo "{\"rule\":\"2b_fake_breakout\",\"direction\":\"sell_signal\",\"strength\":\"very_high\",\"note\":\"2B多头假突破-卖出\"}"
    fi
  fi
  
  if [ "$(echo "$prev_close < $low20 && $price > $low20" | bc -l 2>/dev/null)" = "1" ]; then
    local breach=$(echo "scale=2; ($low20-$prev_close)/$low20*100" | bc -l 2>/dev/null)
    if [ "$(echo "$breach < 3" | bc -l 2>/dev/null)" = "1" ]; then
      echo "{\"rule\":\"2b_fake_breakdown\",\"direction\":\"buy_signal\",\"strength\":\"very_high\",\"note\":\"2B空头假跌破-买入\"}"
    fi
  fi
}

# 脚本路径缓存（在source时解析）
_RULES_DIR_HR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd 2>/dev/null)"

rule_historical_resistance() {
  local code="$1" price="$3" name="$2"
  local rule_dir="$_RULES_DIR_HR"
  local signal_dir="$(cd "$rule_dir/.." 2>/dev/null && pwd)"
  [ -z "$signal_dir" ] && return
  local cache="$signal_dir/cache/${code}.day"
  [ ! -f "$cache" ] || [ "$(wc -l < "$cache")" -lt 15 ] && return
  
  local py_script="$rule_dir/historical_resistance.py"
  [ ! -f "$py_script" ] && return
  
  local result=$(python3 "$py_script" "$cache" "$price" 2>/dev/null)
  [ -n "$result" ] && echo "$result"
}

rule_density_zone() {
  local price="$3" ma20="${12}"
  [ -z "$ma20" ] && return
  local dev=$(echo "scale=2; ($price-$ma20)/$ma20*100" | bc -l 2>/dev/null | sed 's/^-//')
  [ "$(echo "$dev < 1.5" | bc -l 2>/dev/null)" = "1" ] && \
    echo "{\"rule\":\"density_zone\",\"direction\":\"neutral\",\"ma20\":$ma20,\"strength\":\"info\",\"note\":\"20日线附近-筹码密集\"}"
}

rule_chip_analysis() {
  local code="$1" price="$3"
  local rule_dir="$_RULES_DIR_HR"
  local signal_dir="$(cd "$rule_dir/.." 2>/dev/null && pwd)"
  [ -z "$signal_dir" ] && return
  local cache="$signal_dir/cache/${code}.day"
  [ ! -f "$cache" ] || [ "$(wc -l < "$cache")" -lt 15 ] && return
  
  local py_script="$rule_dir/chip_distribution.py"
  [ ! -f "$py_script" ] && return
  
  local result=$(python3 "$py_script" "$cache" "$price" 2>/dev/null)
  [ -n "$result" ] && echo "$result"
}

rule_shrink_then_break() {
  local code="$1" price="$3" vol="${9}" high20="${15}"
  [ -z "$high20" ] || [ "$(echo "$price > $high20" | bc -l 2>/dev/null)" != "1" ] && return
  local cache="$SIGNAL_DIR/cache/${code}.day"
  [ ! -f "$cache" ] || [ "$(wc -l < "$cache")" -lt 7 ] && return
  local lines=$(wc -l < "$cache")
  local avg5vol=$(tail -5 "$cache" | head -4 | awk '{s+=$2} END{printf "%.0f", s/4}')
  [ -z "$avg5vol" ] || [ "$avg5vol" = "0" ] && return
  local had_shrink=0
  for i in 3 4 5; do [ "$lines" -lt "$i" ] && continue
    local v=$(tail -"$i" "$cache" | head -1 | awk '{print $2}')
    [ -n "$v" ] && [ "$(echo "$v < $avg5vol * 0.7" | bc -l 2>/dev/null)" = "1" ] && had_shrink=1 && break
  done
  if [ "$had_shrink" -eq 1 ]; then
    echo "{\"rule\":\"shrink_then_breakout\",\"direction\":\"bullish\",\"strength\":\"very_high\",\"note\":\"缩量整理后放量突破-经典启动形态\"}"
  fi
}
