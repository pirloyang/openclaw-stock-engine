#!/bin/bash
# price.sh — 价格行为规则（纯计算）
# args: code name price change open high low yclose vol ma5 ma10 ma20 ma60 avg5v high20 low20 dif prev_dif prev_close mkt_sh mkt_cy

rule_price_breakout() {
  local change="$4"; local abs=$(echo "$change" | sed 's/^-//' | tr -d '%')
  [ "$(echo "$abs > 2" | bc -l 2>/dev/null)" != "1" ] && return
  local dir="up"; [ "${change:0:1}" = "-" ] && dir="down"
  local level="L1"; [ "$(echo "$abs > 4" | bc -l 2>/dev/null)" = "1" ] && level="L2"; [ "$(echo "$abs > 7" | bc -l 2>/dev/null)" = "1" ] && level="L3"
  local direction="$dir"; [ "$dir" = "down" ] && direction="bearish"
  echo "{\"rule\":\"price_action\",\"direction\":\"${direction}\",\"magnitude\":$abs,\"level\":\"$level\",\"note\":\"价格异动${change}%\"}"
}

rule_limit_filter() {
  local code="$1" name="$2" price="$3" change="$4" high="$6" low="$7" yclose="$8" vol="${9}"
  local abs_chg=$(echo "$change" | sed 's/^-//' | tr -d '%')
  [ "$(echo "$abs_chg < 9" | bc -l 2>/dev/null)" = "1" ] && return
  
  if [ "${change:0:1}" != "-" ]; then
    # 涨停板：涨幅>9%且接近最高价
    local chk=$(echo "$price >= $high * 0.995" | bc -l 2>/dev/null)
    [ "$chk" = "1" ] && \
      echo "{\"rule\":\"limit_up\",\"direction\":\"no_add\",\"strength\":\"high\",\"note\":\"涨停板${change}%,封板状态不做追涨\"}"
  else
    # 跌停板
    local chk=$(echo "$price <= $low * 1.005" | bc -l 2>/dev/null)
    [ "$chk" = "1" ] && \
      echo "{\"rule\":\"limit_down\",\"direction\":\"bearish_urgent\",\"strength\":\"very_high\",\"note\":\"跌停板${change}%,不建议接飞刀\"}"
  fi
}
