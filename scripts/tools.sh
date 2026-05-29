#!/bin/bash
# ============================================================
# 统一数据源 - 所有cron调用的唯一入口
# 用法: bash tools.sh {monitor|holdings|history|signals}
# ============================================================

get_holdings_text() {
  grep "^### 持仓" -A 15 /root/.openclaw/workspace/TOOLS.md | grep "^- " | \
    sed -n 's/^- \([^ ]*\) \([0-9]*\)（\([0-9]*\)股，成本\([0-9.]*\).*/\2 \1 \3 \4/p'
}

get_history_text() {
  grep "历史持仓自选池" -A 60 /root/.openclaw/workspace/TOOLS.md | grep "^- " | \
    sed -n 's/^- \([^ ]*\) \([0-9]*\) .*/\2 \1/p'
}

get_all_codes() {
  # 先加指数（四大指数前置，用于市场环境过滤器）
  echo -n "sh000001,sz399001,sz399006,sh000688,"
  # 持仓
  while read code _ _ _; do
    [[ $code == 6* ]] && echo -n "sh${code}," || echo -n "sz${code},"
  done < <(get_holdings_text)
  # 历史自选池
  while read code _; do
    [[ $code == 6* ]] && echo -n "sh${code}," || echo -n "sz${code},"
  done < <(get_history_text)
  # ETF + 监控标的
  echo -n "sh516640,sz159667,sz159858,sz159928,sh512400,"
  echo -n "sh688008,sz300308,sz300394,sz002230,sz300750,sz300502,sh600522,"
  # 自选监控
  echo -n "sz300456,sz002281,sz300620,sh601138,sz000977,sz300476,sz000034,sz002837,sz300499,sz301018,sz300738,sz300383,sz001309,sz300475,sz002119,sz300302,sz300661,sh688798,sz300223,sh603881,sz300857,sz000032,sz002335,sh600602,sz300660,sz002881,sz000636,sz000988,"
  # 风口监控-原油暴跌受益（航空+化工）
  echo -n "sh601111,sh600029,sh600115,sh600309,sz002648,sz002493,"
  # 风口监控-半导体设备延伸
  echo -n "sz002371,sh688012,"  # 北方华创+中微公司
  # 风口监控-存储纵深
  echo -n "sz301308,sh688525,"  # 江波龙+佰维存储
  # 商业航天
  echo -n "sh600118,sz002025,sz300045,sh688568,sz300762,sh600343,sz300455,sh688523,"
  # 核心自选（辉哥君弘截图）
  echo -n "sz301306,sz002465,sh600391,sh600592,sz301005,sz000901,sz002682,sh600151,"
  # 自选（完整君弘截图）
  echo -n "sz000551,sz300265,sz002361,sz003009,sh600345,sz002151,"
  # PCB产业链（2026-05-22新增，2026-05-23加东山精密）
  echo -n "sh600183,sz002916,sz002938,sz002384"
  # 有色/新材料（2026-05-23加安泰科技）
  echo -n ",sz000969"
}

fetch_prices() {
  curl -s --max-time 10 "https://qt.gtimg.cn/q=$(get_all_codes)" 2>/dev/null | iconv -f GBK -t UTF-8 2>/dev/null
}

get_price() { echo "$1" | awk -F'~' -v idx="$2" '{print $idx}'; }

calc_signal() {
  local change="$1"
  local a=$(echo "$change" | tr -d '%+' | sed 's/^-//')
  if [ "$(echo "$a > 7" | bc -l 2>/dev/null)" = "1" ]; then
    echo "🔴"
  elif [ "$(echo "$a > 4" | bc -l 2>/dev/null)" = "1" ]; then
    echo "⚡⚡"
  elif [ "$(echo "$a > 2" | bc -l 2>/dev/null)" = "1" ]; then
    echo "⚡"
  fi
}

monitor_report() {
  local RAW=$(fetch_prices)

  echo "════════════════════════════════════"
  echo "📊 $(date '+%H:%M') 盘中实时"
  echo "════════════════════════════════════"
  echo ""

  echo "【大盘指数】"
  for idx in "sh000001|上证指数" "sz399001|深证成指" "sz399006|创业板指" "sh000688|科创50"; do
    local idx_code="${idx%|*}" idx_name="${idx#*|}"
    local d=$(echo "$RAW" | grep -m1 "$idx_code")
    local p=$(get_price "$d" 4)
    local c=$(get_price "$d" 33)
    local h=$(get_price "$d" 34)
    local l=$(get_price "$d" 35)
    local a=$(get_price "$d" 44)
    [ -z "$p" ] && continue
    local s=$(calc_signal "$c")
    echo "  $idx_name: $p ($c) 振幅${a}% $s"
  done
  echo ""

  echo "【持仓】"
  while read code name shares cost; do
    local pfx="sh"; [[ $code != 6* ]] && pfx="sz"
    local d=$(echo "$RAW" | grep -m1 "${pfx}${code}")
    local p=$(get_price "$d" 4)
    local c=$(get_price "$d" 33)
    local pl=$(echo "scale=0; ($p-$cost)*$shares" | bc 2>/dev/null)
    local s=$(calc_signal "$c")
    echo "  $name: $p ($c) 盈亏$pl $s"
  done < <(get_holdings_text)

  echo ""
  echo "【ETF】"
  while IFS='|' read -r pfx name; do
    local d=$(echo "$RAW" | grep -m1 "$pfx")
    local p=$(get_price "$d" 4)
    local c=$(get_price "$d" 33)
    [ -z "$p" ] && continue
    local s=$(calc_signal "$c")
    echo "  $name: $p ($c)$s"
  done <<< "sh516640|芯片ETF
sz159667|工业母机ETF
sz159858|创新药ETF
sz159928|消费ETF
sh512400|有色ETF"

  echo ""
  echo "【概念】"
  while IFS='|' read -r pfx name concept; do
    local d=$(echo "$RAW" | grep -m1 "$pfx")
    local p=$(get_price "$d" 4)
    local c=$(get_price "$d" 33)
    [ -z "$p" ] && continue
    local s=$(calc_signal "$c")
    echo "  [$concept] $name: $p ($c)$s"
  done <<< "sh688008|澜起科技|HBM
sz300308|中际旭创|CPO
sz300394|天孚通信|CPO
sz002230|科大讯飞|脑机
sz300750|宁德时代|储能
sz300502|新易盛|光模块"

  echo ""
  echo "【商业航天】"
  while IFS='|' read -r pfx name; do
    local d=$(echo "$RAW" | grep -m1 "$pfx")
    local p=$(get_price "$d" 4)
    local c=$(get_price "$d" 33)
    [ -z "$p" ] && continue
    local s=$(calc_signal "$c")
    echo "  $name: $p ($c)$s"
  done <<< "sh600118|中国卫星
sz002025|航天电器
sz300045|华力创通
sh688568|中科星图
sz300762|上海瀚讯
sh600343|航天动力
sz300455|航天智装
sh688523|航天环宇"

  echo ""
  echo "【核心自选】"
  while IFS='|' read -r pfx name; do
    local d=$(echo "$RAW" | grep -m1 "$pfx")
    local p=$(get_price "$d" 4)
    local c=$(get_price "$d" 33)
    [ -z "$p" ] && continue
    local s=$(calc_signal "$c")
    echo "  $name: $p ($c)$s"
  done <<< "sz301306|西测测试
sz002465|海格通信
sh600391|航发科技
sh600592|龙溪股份
sz301005|超捷股份
sz000901|航天科技
sz002682|龙洲股份
sh600151|航天机电"

  echo ""
  echo "【历史自选】"
  while read code name; do
    local pfx="sh"; [[ $code != 6* ]] && pfx="sz"
    local d=$(echo "$RAW" | grep -m1 "${pfx}${code}")
    local p=$(get_price "$d" 4)
    local c=$(get_price "$d" 33)
    [ -z "$p" ] && continue
    local s=$(calc_signal "$c")
    local tag=""
    [ -n "$s" ] && tag=" $s⚠️"
    echo "  $name($code): $p ($c)$tag"
  done < <(get_history_text)

  echo "【自选监控】"
  for mon in "sz300456|赛微电子" "sz002281|光迅科技" "sz300620|光库科技" "sh601138|工业富联" "sz000977|浪潮信息" "sz300476|胜宏科技" "sz000034|神州数码" "sz002837|英维克" "sz300499|高澜股份" "sz301018|申菱环境" "sz300738|奥飞数据" "sz300383|光环新网" "sz001309|德明利" "sz300475|香农芯创" "sz002119|康强电子" "sz300302|同有科技" "sz300661|圣邦股份" "sh688798|艾为电子" "sz300223|北京君正" "sh603881|数据港" "sz300857|协创数据" "sz000032|深桑达A" "sz002335|科华数据" "sh600602|云赛智联" "sz000551|创元科技" "sz300265|通光线缆" "sz002361|神剑股份" "sz003009|中天火箭" "sh600345|长江通信" "sz002151|北斗星通"; do
    local m_code="${mon%|*}" m_name="${mon#*|}"
    local d=$(echo "$RAW" | grep -m1 "$m_code")
    local p=$(get_price "$d" 4)
    local c=$(get_price "$d" 33)
    [ -z "$p" ] && continue
    local s=$(calc_signal "$c")
    echo "  $m_name: $p ($c)$s"
  done
  echo ""
  echo "✅ $(date '+%H:%M:%S')"
}

case "${1:-monitor}" in
  monitor) monitor_report ;;
  holdings) get_holdings_text ;;
  history) get_history_text ;;
  signals) monitor_report ;;
  *) echo "用法: $0 {monitor|holdings|history|signals}"; exit 1 ;;
esac
