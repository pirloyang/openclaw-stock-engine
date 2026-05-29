#!/bin/bash
# market_filter.sh — 大盘环境过滤器（最高优先级约束）
# args: code name price change open high low yclose vol ma5 ma10 ma20 ma60 avg5v high20 low20 dif prev_dif prev_close mkt_sh mkt_cy
# mkt_sh = 上证涨跌幅(%) mkt_cy = 创业板涨跌幅(%)

# 已对全池输出一次环境信号后不再重复（用状态文件防重复）
FILTER_FLAG="/tmp/market_filter_active"

rule_market_environment() {
  local mkt_sh="${20:-0}" mkt_cy="${21:-0}" name="$2"
  
  # 从mkt_sh判断：高开低走 or 单边下跌
  local is_negative=$(echo "$mkt_sh < 0" | bc -l 2>/dev/null)
  local abs_sh=$(echo "$mkt_sh" | sed 's/^-//' 2>/dev/null)
  
  # 早盘已经执行过就不重复了
  [ -f "$FILTER_FLAG" ] && return
  
  if [ "$is_negative" = "1" ] && [ "$(echo "$abs_sh > 0.5" | bc -l 2>/dev/null)" = "1" ]; then
    echo "{\"rule\":\"market_filter_active\",\"direction\":\"suspend_all_buy\",\"sh_change\":$mkt_sh,\"cy_change\":$mkt_cy,\"strength\":\"very_high\",\"note\":\"【市场过滤器激活】上证${mkt_sh}%，创业板${mkt_cy}%→当日所有买入操作暂停，持仓买入信号自动降级为观察\"}"
    touch "$FILTER_FLAG"
  fi
}

rule_market_reset() {
  # 每日重置过滤器
  local today=$(date +%Y%m%d)
  local flag_date=""
  [ -f "$FILTER_FLAG" ] && flag_date=$(date -r "$FILTER_FLAG" +%Y%m%d 2>/dev/null)
  [ "$flag_date" != "$today" ] && rm -f "$FILTER_FLAG" 2>/dev/null
}
