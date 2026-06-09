#!/bin/bash
# rsi.sh — RSI超买超卖信号
# args: code name price change open high low yclose vol ma5 ma10 ma20 ma60 avg5v high20 low20 dif prev_dif prev_close mkt_sh mkt_cy
# 依赖 cache 日线数据计算 RSI(14)

rule_rsi() {
  local code="$1" name="$2" price="$3"
  local cache="$SIGNAL_DIR/cache/${code}.day"
  [ ! -f "$cache" ] && return
  local lines=$(wc -l < "$cache")
  [ "$lines" -lt 20 ] && return  # RSI(14)至少需要15根

  local rsi=$(python3 -c "
f=open('$cache'); lines=f.readlines(); f.close()
prices=[float(l.split()[0]) for l in lines[-21:] if len(l.split()) in (2,3)]  # 取21天算RSI(14)，跳过OHLC行
if len(prices) < 15:
    exit()
gains,losses=[],[]
for i in range(1,15):
    d=prices[-i]-prices[-i-1]
    gains.append(max(0,d)); losses.append(max(0,-d))
avg_gain=sum(gains)/14; avg_loss=sum(losses)/14
if avg_loss==0:
    print(100)
else:
    rs=avg_gain/avg_loss
    rsi=100-100/(1+rs)
    print(f'{rsi:.1f}')
" 2>/dev/null)

  [ -z "$rsi" ] && return
  [ "$(echo "$rsi == 100" | bc -l 2>/dev/null)" = "1" ] && return

  if [ "$(echo "$rsi > 70" | bc -l 2>/dev/null)" = "1" ]; then
    echo "{\"rule\":\"rsi_overbought\",\"direction\":\"sell_signal\",\"rsi\":$rsi,\"strength\":\"medium\",\"note\":\"RSI超买(${rsi})—超买区警惕回调\"}"
  elif [ "$(echo "$rsi < 30" | bc -l 2>/dev/null)" = "1" ]; then
    echo "{\"rule\":\"rsi_oversold\",\"direction\":\"buy_signal\",\"rsi\":$rsi,\"strength\":\"medium\",\"note\":\"RSI超卖(${rsi})—超卖区关注反弹机会\"}"
  fi
}
