#!/bin/bash
# sector.sh — 板块联动/大盘对照规则 + 板块相对强度（用预取的大盘数据和ETF数据）
# args: code name price change open high low yclose vol ma5 ma10 ma20 ma60 avg5v high20 low20 dif prev_dif prev_close mkt_sh mkt_cy
# 全局变量: ETF_CHG_CHIP ETF_CHG_MACHINE ETF_CHG_INNOVATION ETF_CHG_CONSUME ETF_CHG_METAL

rule_should_rise_fail() {
  local change="$4" mkt_sh="${20}" mkt_cy="${21}" name="$2" code="$1"
  [ "${change:0:1}" != "-" ] && return
  local abs=$(echo "$change" | sed 's/^-//' | tr -d '%')
  
  if [ "$(echo "$mkt_sh > 0.5 || $mkt_cy > 0.5" | bc -l 2>/dev/null)" = "1" ]; then
    if [ "$(echo "$abs > 2" | bc -l 2>/dev/null)" = "1" ]; then
      echo "{\"rule\":\"should_rise_fail\",\"direction\":\"bearish_urgent\",\"sh\":\"$mkt_sh\",\"cy\":\"$mkt_cy\",\"strength\":\"very_high\",\"note\":\"【该涨不涨】大盘涨但${name}(${code})跌${change}%\"}"
    elif [ "$(echo "$abs > 1" | bc -l 2>/dev/null)" = "1" ]; then
      echo "{\"rule\":\"should_rise_fail_mild\",\"direction\":\"bearish_warn\",\"sh\":\"$mkt_sh\",\"cy\":\"$mkt_cy\",\"strength\":\"medium\",\"note\":\"该涨不涨-大盘涨个股跌\"}"
    fi
  fi
}

rule_should_fall_strong() {
  local change="$4" mkt_sh="${20}" mkt_cy="${21}" name="$2" code="$1"
  [ "${change:0:1}" = "-" ] && return
  local abs=$(echo "$change" | sed 's/^-//' | tr -d '%')
  
  if [ "$(echo "$mkt_sh < -0.5 || $mkt_cy < -0.5" | bc -l 2>/dev/null)" = "1" ]; then
    if [ "$(echo "$abs > 2" | bc -l 2>/dev/null)" = "1" ]; then
      echo "{\"rule\":\"should_fall_strong\",\"direction\":\"bullish_urgent\",\"sh\":\"$mkt_sh\",\"cy\":\"$mkt_cy\",\"strength\":\"very_high\",\"note\":\"【逆势走强】大盘跌但${name}(${code})涨${change}%\"}"
    fi
  fi
}

# stock→板块ETF映射（从自选池提取，约60只标的覆盖5个核心板块）
_sector_etf() {
  local code="$1"
  case "$code" in
    688008|603893|002185|002119|002049|688798|603986|300223|688525|300456|300661|002371|688012|002916|300476|600183) echo "chip" ;;
    002463|300394|300308|300502|002281|300620|600487|002837|300499|300738|300383|600105|601138|300115|002050|002594|300750|002384|002938|002881) echo "chip" ;;
    002553|300809|600592|301005|300450|300660) echo "machine" ;;
    300142) echo "innovation" ;;
    600809) echo "consume" ;;
    002428|601600|603799|603993|600549|002648|002493|000969) echo "metal" ;;
    *) echo "" ;;
  esac
}

rule_sector_strength() {
  local code="$1" name="$2" change="$4"
  local sector=$(_sector_etf "$code")
  [ -z "$sector" ] && return
  
  local etf_chg=0
  case "$sector" in
    chip) etf_chg=${ETF_CHG_CHIP:-0} ;;
    machine) etf_chg=${ETF_CHG_MACHINE:-0} ;;
    innovation) etf_chg=${ETF_CHG_INNOVATION:-0} ;;
    consume) etf_chg=${ETF_CHG_CONSUME:-0} ;;
    metal) etf_chg=${ETF_CHG_METAL:-0} ;;
  esac
  [ "$(echo "$etf_chg == 0" | bc -l 2>/dev/null)" = "1" ] && return
  
  local diff=$(echo "scale=2; $change - $etf_chg" | bc -l 2>/dev/null)
  if [ "$(echo "$diff > 3" | bc -l 2>/dev/null)" = "1" ]; then
    echo "{\"rule\":\"outperform_sector\",\"direction\":\"bullish\",\"strength\":\"medium\",\"note\":\"跑赢板块+${diff}%,个股${change}% vs 板块${etf_chg}%\"}"
  elif [ "$(echo "$diff < -3" | bc -l 2>/dev/null)" = "1" ]; then
    echo "{\"rule\":\"underperform_sector\",\"direction\":\"bearish_warn\",\"strength\":\"medium\",\"note\":\"跑输板块${diff}%,个股${change}% vs 板块${etf_chg}%\"}"
  fi
}
