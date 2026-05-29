#!/bin/bash
# volume.sh — 量能信号（纯计算，用缓存中的成交量数据）
# args: code name price change open high low yclose vol ma5 ma10 ma20 ma60 avg5v high20 low20 dif prev_dif prev_close mkt_sh mkt_cy

rule_volume_ratio() {
  local vol="${9}" avg10v_vol="${14}" change="$4"
  [ -z "$vol" ] || [ "$vol" = "0" ] || [ -z "$avg10v_vol" ] || [ "$avg10v_vol" = "0" ] && return
  
  local ratio=$(echo "scale=2; $vol / $avg10v_vol" | bc -l 2>/dev/null)
  local ratio_pct=$(echo "scale=0; $ratio * 100" | bc -l 2>/dev/null)
  
  if [ "$(echo "$ratio > 2" | bc -l 2>/dev/null)" = "1" ]; then
    echo "{\"rule\":\"volume_surge\",\"direction\":\"heavy_vol\",\"ratio\":${ratio_pct},\"strength\":\"high\",\"note\":\"放量${ratio_pct}%-天量有妖不追\"}"
  elif [ "$(echo "$ratio < 0.7" | bc -l 2>/dev/null)" = "1" ]; then
    echo "{\"rule\":\"volume_shrink\",\"direction\":\"light_vol\",\"ratio\":${ratio_pct},\"strength\":\"medium\",\"note\":\"缩量至${ratio_pct}%-洗盘特征\"}"
  fi
}

rule_turnover() {
  local price="$3" name="$2"
  local turnover="${STOCK_TURNOVER:-0}" vol_ratio="${VOL_RATIO_GLOBAL:-0}"
  [ "$(echo "$turnover == 0" | bc -l 2>/dev/null)" = "1" ] && return

  if [ "$(echo "$turnover > 10" | bc -l 2>/dev/null)" = "1" ]; then
    echo "{\"rule\":\"turnover_abnormal\",\"direction\":\"heavy_vol\",\"strength\":\"high\",\"note\":\"换手率${turnover}%-极度活跃,天量警惕出货\"}"
  elif [ "$(echo "$turnover > 7" | bc -l 2>/dev/null)" = "1" ]; then
    echo "{\"rule\":\"turnover_high\",\"direction\":\"heavy_vol\",\"turnover\":$turnover,\"strength\":\"medium\",\"note\":\"换手率${turnover}%-非常活跃\"}"
  elif [ "$(echo "$turnover > 3" | bc -l 2>/dev/null)" = "1" ]; then
    echo "{\"rule\":\"turnover_active\",\"direction\":\"active\",\"turnover\":$turnover,\"strength\":\"info\",\"note\":\"换手率${turnover}%-活跃\"}"
  fi
}

rule_volume_price() {
  local price="$3" change="$4" vol="${9}" avg10v_vol="${14}"
  [ -z "$vol" ] || [ "$vol" = "0" ] || [ -z "$avg10v_vol" ] || [ "$avg10v_vol" = "0" ] && return
  
  local abs=$(echo "$change" | sed 's/^-//' | tr -d '%')
  local dir="${change:0:1}"
  local ratio=$(echo "scale=2; $vol / $avg10v_vol" | bc -l 2>/dev/null)
  
  # 价涨量增
  if [ "$dir" != "-" ] && [ "$(echo "$ratio > 1.3 && $abs > 2" | bc -l 2>/dev/null)" = "1" ]; then
    echo "{\"rule\":\"vol_up_with_price\",\"direction\":\"bullish\",\"strength\":\"high\",\"note\":\"价涨量增-健康\"}"
  fi
  # 价涨量缩
  if [ "$dir" != "-" ] && [ "$(echo "$ratio < 0.8 && $abs > 2" | bc -l 2>/dev/null)" = "1" ]; then
    echo "{\"rule\":\"vol_up_no_vol\",\"direction\":\"bearish_warn\",\"strength\":\"medium\",\"note\":\"价涨量缩-乏力\"}"
  fi
  # 价跌量增
  if [ "$dir" = "-" ] && [ "$(echo "$ratio > 1.3 && $abs > 2" | bc -l 2>/dev/null)" = "1" ]; then
    echo "{\"rule\":\"vol_down_with_vol\",\"direction\":\"bearish\",\"strength\":\"high\",\"note\":\"价跌量增-出货\"}"
  fi
  # 价跌量缩
  if [ "$dir" = "-" ] && [ "$(echo "$ratio < 0.7 && $abs > 1" | bc -l 2>/dev/null)" = "1" ]; then
    echo "{\"rule\":\"vol_down_shrink\",\"direction\":\"washout\",\"strength\":\"medium\",\"note\":\"价跌量缩-洗盘\"}"
  fi
}
