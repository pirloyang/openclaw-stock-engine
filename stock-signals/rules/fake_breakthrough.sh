#!/bin/bash
# fake_breakthrough.sh — 假突破过滤规则
# 检测高开低走+巨量=假突破，覆盖所有买入信号
# 遵循标准shell规则参数格式
# args: code name price change open high low yclose vol ma5 ma10 ma20 ma60 avg5v high20 low20 dif prev_dif prev_close mkt_sh mkt_cy

rule_fake_breakthrough() {
  local price="$3" open="$5" high="$6" low="$7" yclose="$8" vol="${9}" code="$1"
  [ -z "$price" ] || [ -z "$open" ] || [ -z "$high" ] || [ -z "$low" ] || [ -z "$yclose" ] || [ -z "$vol" ] && return
  
  # 昨收为0时不计算
  [ "$(echo "$yclose == 0" | bc -l 2>/dev/null)" = "1" ] && return

  # 条件1: 高开（开盘＞昨收）
  local is_gap_up=$(echo "$open > $yclose" | bc -l 2>/dev/null)
  [ "$is_gap_up" != "1" ] && return

  # 条件2: 高开幅度≥5%
  local gap_pct=$(echo "scale=2; ($open - $yclose) / $yclose * 100" | bc -l 2>/dev/null)
  [ "$(echo "$gap_pct < 5" | bc -l 2>/dev/null)" = "1" ] && return

  # 条件3: 收盘低于开盘（高开低走）
  local is_gap_down=$(echo "$price < $open" | bc -l 2>/dev/null)
  [ "$is_gap_down" != "1" ] && return

  # 条件4: 从最高点回落幅度≥5%（收盘 << 最高）
  local decline_from_high=$(echo "scale=2; ($high - $price) / $high * 100" | bc -l 2>/dev/null)
  [ "$(echo "$decline_from_high < 5" | bc -l 2>/dev/null)" = "1" ] && return

  # 条件5: 量比≥2倍（与10日均量比较）
  local avg10v="${14}"
  [ -z "$avg10v" ] || [ "$(echo "$avg10v == 0" | bc -l 2>/dev/null)" = "1" ] && return
  local vol_ratio=$(echo "scale=2; $vol / $avg10v" | bc -l 2>/dev/null)
  [ "$(echo "$vol_ratio < 2" | bc -l 2>/dev/null)" = "1" ] && return

  # 全部条件满足 → 假突破
  local gap_pct_fmt=$(echo "$gap_pct" | awk '{printf "%.1f", $1}')
  local decline_fmt=$(echo "$decline_from_high" | awk '{printf "%.1f", $1}')
  local vol_ratio_fmt=$(echo "$vol_ratio" | awk '{printf "%.1f", $1}')
  
  echo "{\"rule\":\"fake_breakthrough\",\"direction\":\"bearish\",\"strength\":\"very_high\",\"note\":\"假突破-跳空+${gap_pct_fmt}%高开低走收跌,回落${decline_fmt}%,量${vol_ratio_fmt}x均量-天量出货\"}"
}
