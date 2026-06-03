#!/bin/bash
# trend.sh — 均线+MACD信号（纯计算）
# args: code name price change open high low yclose vol ma5 ma10 ma20 ma60 avg5v high20 low20 dif prev_dif prev_close mkt_sh mkt_cy

rule_ma_arrangement() {
  local price="$3" ma5="${10}" ma10="${11}" ma20="${12}" ma60="${13}"
  [ -z "$ma5" ] || [ -z "$ma20" ] && return
  
  if [ -n "$ma60" ]; then
    if [ "$(echo "$ma5 > $ma10 && $ma10 > $ma20 && $ma20 > $ma60" | bc -l 2>/dev/null)" = "1" ]; then
      echo "{\"rule\":\"bullish_arrangement\",\"direction\":\"strong_hold\",\"ma5\":$ma5,\"ma10\":$ma10,\"ma20\":$ma20,\"ma60\":$ma60,\"strength\":\"very_high\",\"note\":\"均线多头排列5>10>20>60\"}"
      return
    fi
  fi
  
  if [ "$(echo "$ma5 < $ma10 && $ma10 < $ma20" | bc -l 2>/dev/null)" = "1" ]; then
    echo "{\"rule\":\"bearish_arrangement\",\"direction\":\"bearish\",\"ma5\":$ma5,\"ma10\":$ma10,\"ma20\":$ma20,\"strength\":\"high\",\"note\":\"均线空头排列5<10<20\"}"
  fi
}

rule_ma_cross() {
  local price="$3" ma5="${10}" ma20="${12}" ma10="${11}"
  [ -z "$ma5" ] || [ -z "$ma20" ] && return
  
  local diff_5_20=$(echo "scale=2; $ma5 - $ma20" | bc -l 2>/dev/null | sed 's/^-//')
  
  if [ "$(echo "$ma5 > $ma20 && $price > $ma5" | bc -l 2>/dev/null)" = "1" ] && [ "$(echo "$diff_5_20 < $ma20 * 0.03" | bc -l 2>/dev/null)" = "1" ]; then
    echo "{\"rule\":\"ma_golden_cross\",\"direction\":\"buy_signal\",\"ma5\":$ma5,\"ma20\":$ma20,\"strength\":\"high\",\"note\":\"5日线金叉20日线\"}"
  fi
  
  if [ "$(echo "$ma5 < $ma20 && $price < $ma5" | bc -l 2>/dev/null)" = "1" ] && [ "$(echo "$diff_5_20 < $ma20 * 0.03" | bc -l 2>/dev/null)" = "1" ]; then
    echo "{\"rule\":\"ma_death_cross\",\"direction\":\"sell_signal\",\"ma5\":$ma5,\"ma20\":$ma20,\"strength\":\"high\",\"note\":\"5日线死叉20日线-减仓\"}"
  fi
}

rule_ma5_exclusion() {
  local price="$3" ma5="${10}"
  [ -z "$ma5" ] && return
  local dev=$(echo "scale=1; ($price - $ma5)/$ma5*100" | bc -l 2>/dev/null | sed 's/^-//')
  [ "$(echo "$dev > 5" | bc -l 2>/dev/null)" = "1" ] && \
    echo "{\"rule\":\"ma5_gap\",\"direction\":\"no_add\",\"deviation\":$dev,\"strength\":\"high\",\"note\":\"偏离MA5达${dev}%-禁止加仓只持有\"}"
}

rule_ma20_exclusion() {
  local price="$3" ma20="${12}"
  [ -z "$ma20" ] && return
  local dev=$(echo "scale=1; ($price - $ma20)/$ma20*100" | bc -l 2>/dev/null | sed 's/^-//')
  [ "$(echo "$dev > 30" | bc -l 2>/dev/null)" = "1" ] && \
    echo "{\"rule\":\"ma20_gap\",\"direction\":\"exclude_buy\",\"deviation\":$dev,\"strength\":\"very_high\",\"note\":\"偏离MA20达${dev}%-禁止买入\"}"
}

rule_macd_divergence() {
  local price="$3" dif="${17}" prev_dif="${18}" prev_close="${19}" change="$4"
  [ -z "$dif" ] || [ -z "$prev_dif" ] || [ -z "$prev_close" ] && return
  
  if [ "$(echo "$price < $prev_close && $dif > $prev_dif" | bc -l 2>/dev/null)" = "1" ]; then
    echo "{\"rule\":\"macd_bottom_div\",\"direction\":\"buy_signal\",\"strength\":\"very_high\",\"note\":\"MACD底背离-股价新低DIF未新低\"}"
  fi
  if [ "$(echo "$price > $prev_close && $dif < $prev_dif" | bc -l 2>/dev/null)" = "1" ]; then
    echo "{\"rule\":\"macd_top_div\",\"direction\":\"sell_signal\",\"strength\":\"very_high\",\"note\":\"MACD顶背离-股价新高DIF未新高\"}"
  fi
}

rule_macd_zone() {
  local dif="${17}"; [ -z "$dif" ] && return
  if [ "$(echo "$dif > 0" | bc -l 2>/dev/null)" = "1" ]; then
    echo "{\"rule\":\"macd_above_zero\",\"direction\":\"bullish_context\",\"dif\":$dif,\"strength\":\"info\",\"note\":\"MACD零轴上方-强势\"}"
  else
    echo "{\"rule\":\"macd_below_zero\",\"direction\":\"bearish_context\",\"dif\":$dif,\"strength\":\"info\",\"note\":\"MACD零轴下方-弱势\"}"
  fi
}

# v3.0: MACD金叉/死叉检测（DIF vs DEA）
# 参数: price dif prev_dif ... (需要额外从cache计算DEA)
rule_macd_cross() {
  local dif="${17}" prev_dif="${18}"
  [ -z "$dif" ] || [ -z "$prev_dif" ] && return

  # DIF上穿零轴 → 金叉确认（简化判定：prev_dif<0且dif>0）
  if [ "$(echo "$prev_dif < 0 && $dif > 0" | bc -l 2>/dev/null)" = "1" ]; then
    echo "{\"rule\":\"macd_golden_cross\",\"direction\":\"buy_signal\",\"dif\":$dif,\"prev_dif\":$prev_dif,\"strength\":\"high\",\"note\":\"MACD零轴金叉-DIF上穿零轴\"}"
  fi

  # DIF下穿零轴 → 死叉确认
  if [ "$(echo "$prev_dif > 0 && $dif < 0" | bc -l 2>/dev/null)" = "1" ]; then
    echo "{\"rule\":\"macd_death_cross\",\"direction\":\"sell_signal\",\"dif\":$dif,\"prev_dif\":$prev_dif,\"strength\":\"high\",\"note\":\"MACD零轴死叉-DIF下穿零轴\"}"
  fi
}

rule_ma_convergence() {
  local price="$3" ma5="${10}" ma10="${11}" ma20="${12}"
  [ -z "$ma5" ] || [ -z "$ma10" ] || [ -z "$ma20" ] && return

  # 三线最大差距 / MA20
  local max_ma=$(echo "if($ma5 > $ma10) if($ma5 > $ma20) $ma5 else $ma20 else if($ma10 > $ma20) $ma10 else $ma20" | bc -l 2>/dev/null)
  local min_ma=$(echo "if($ma5 < $ma10) if($ma5 < $ma20) $ma5 else $ma20 else if($ma10 < $ma20) $ma10 else $ma20" | bc -l 2>/dev/null)
  local spread=$(echo "scale=2; ($max_ma - $min_ma) / $ma20 * 100" | bc -l 2>/dev/null)

  # 三线聚拢：最大差距<2% → 均线粘合
  if [ "$(echo "$spread < 2" | bc -l 2>/dev/null)" = "1" ]; then
    local mid_ma=$(echo "scale=2; ($ma5 + $ma10 + $ma20) / 3" | bc -l 2>/dev/null)
    if [ "$(echo "$price > $mid_ma" | bc -l 2>/dev/null)" = "1" ]; then
      echo "{\"rule\":\"ma_convergence_up\",\"direction\":\"bullish\",\"spread\":$spread,\"strength\":\"medium\",\"note\":\"均线粘合-价格在上方,变盘向上概率大\"}"
    else
      echo "{\"rule\":\"ma_convergence_down\",\"direction\":\"bearish_warn\",\"spread\":$spread,\"strength\":\"medium\",\"note\":\"均线粘合-价格在下方,密切关注方向\"}"
    fi
  fi
}
