#!/bin/bash
# entry_exit.sh — 入场止损位计算 + 成本线风控
# args: code name price change open high low yclose vol ma5 ma10 ma20 ma60 avg5v high20 low20 dif prev_dif prev_close mkt_sh mkt_cy

rule_entry_stop() {
  local change="$4" open="$5" price="$3"
  [ "${change:0:1}" = "-" ] && return
  local abs=$(echo "$change" | sed 's/^-//' | tr -d '%')
  [ "$(echo "$abs < 3" | bc -l 2>/dev/null)" = "1" ] && return
  [ -z "$open" ] || [ "$open" = "0.000" ] && return
  
  local body=$(echo "scale=2; $price - $open" | bc -l 2>/dev/null)
  [ "$(echo "$body <= 0" | bc -l 2>/dev/null)" = "1" ] && return
  
  local body_50=$(echo "scale=2; $body * 0.5" | bc -l 2>/dev/null)
  local stop=$(echo "scale=2; $price - $body_50" | bc -l 2>/dev/null)
  
  echo "{\"rule\":\"entry_stop_loss\",\"direction\":\"risk_mgmt\",\"body_50pct\":$stop,\"strength\":\"high\",\"note\":\"入场止损:跌破${stop}(阳线实体50%位)出清\"}"
}

# 动态止盈（基于技术位置，无需成本价注入）
rule_trailing_stop() {
  local price="$3" change="$4" high20="${15}" ma20="${12}"
  [ -z "$high20" ] || [ -z "$ma20" ] || [ "$high20" = "0.000" ] || [ "$ma20" = "0.000" ] && return

  # 条件1: 股价高于MA20 10%以上（有浮盈基础）
  local profit_ok=$(echo "$price > $ma20 * 1.10" | bc -l 2>/dev/null)
  [ "$profit_ok" != "1" ] && return

  # 条件2: 从20日高点回撤超过5%
  local pullback=$(echo "scale=2; ($high20 - $price) / $high20 * 100" | bc -l 2>/dev/null)
  if [ "$(echo "$pullback > 8" | bc -l 2>/dev/null)" = "1" ]; then
    echo "{\"rule\":\"trailing_stop_urgent\",\"direction\":\"sell_signal\",\"pullback\":$pullback,\"strength\":\"very_high\",\"note\":\"⚠️动态止盈—从20日高点回撤${pullback}%，建议清仓\"}"
  elif [ "$(echo "$pullback > 5" | bc -l 2>/dev/null)" = "1" ]; then
    echo "{\"rule\":\"trailing_stop\",\"direction\":\"sell_signal\",\"pullback\":$pullback,\"strength\":\"high\",\"note\":\"动态止盈—从20日高点回撤${pullback}%，建议减仓保护利润\"}"
  fi
}
