#!/bin/bash
# ==========================================================
# 每日行情缓存 V3 — 带日期去重的OHLCV缓存
# 用法: bash price_cache.sh {update|status|backfill}
#   update:  每日收盘后增量补最新交易日
#   status:  检查缓存文件行数
#   backfill:全量回补60天（首次部署/加新标的时调用）
# ==========================================================

CACHE_DIR="/root/.openclaw/workspace/stock-signals/cache"
WORKSPACE="/root/.openclaw/workspace"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$CACHE_DIR"

# 统一前缀映射（与backfill_cache.sh一致）
code_to_prefix() {
  local code="$1"
  [[ $code == 6* || $code == 5* || $code == "000001" ]] && { echo "sh"; return; }
  echo "sz"
}

# 获取全池代码列表（与engine.sh对齐）
get_all_codes() {
  bash "$WORKSPACE/scripts/tools.sh" holdings 2>/dev/null | awk '{print $1}'
  bash "$WORKSPACE/scripts/tools.sh" history  2>/dev/null | awk '{print $1}'
  # 监控池中不在持仓/历史中的额外标的
  for code in 000001 399001 399006 516640 159667 159858 159928 512400 \
    688008 300308 300394 002230 300750 300502 600522 \
    300456 002281 300620 601138 000977 300476 000034 002837 300499 301018 \
    300738 300383 001309 300475 002119 300302 300661 688798 300223 603881 300857 \
    000032 002335 600602 600118 002025 300045 688568 300762 600343 300455 688523 \
    301306 002465 600391 600592 301005 000901 002682 600151 000551 300265 002361 \
    003009 600345 002151; do
    echo "$code"
  done
}

# 从新浪API拉取60天OHLCV
fetch_60d() {
  local code="$1" pfx="$(code_to_prefix "$code")"
  curl -s --max-time 10 \
    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=${pfx}${code},day,,,60,qfq" 2>/dev/null
}
parse_ohlcv() {
  local raw="$1"
  echo "$raw" | python3 -c "
import sys, json
raw = sys.stdin.read()
if '=' in raw:
    raw = raw.split('=')[1].split(';')[0]
try:
    data = json.loads(raw)
    d = data.get('data', {})
    keys = list(d.keys())
    if not keys:
        sys.exit(0)
    kline = d[keys[0]].get('qfqday') or d[keys[0]].get('day') or []
    if isinstance(kline, list):
        for row in kline:
            if len(row) >= 6:
                close = row[2]
                vol = float(row[5]) * 100
                day = row[0]
                open_ = row[1]
                high = row[3]
                low = row[4]
                print(f'{close} {open_} {high} {low} {int(vol)} {day}')
except: pass" 2>/dev/null
}

# --------------- 全量回补时也用新格式（兼容旧格式升级） ---------------

parse_ohlcv_v2() {
  local raw="$1"
  [ -z "$raw" ] && return 1
  echo "$raw" | python3 -c '
import sys, json
try:
    txt = sys.stdin.read()
    if "=" in txt:
        txt = txt.split("=",1)[1].split(";")[0]
    data = json.loads(txt)
    d = data.get("data", {})
    keys = list(d.keys())
    if not keys:
        sys.exit(0)
    kline = d[keys[0]].get("qfqday") or d[keys[0]].get("day") or []
    if isinstance(kline, list):
        for row in kline:
            if len(row) >= 6:
                close = row[2]
                open_ = row[1]
                high = row[3]
                low = row[4]
                vol = float(row[5]) * 100
                day = row[0]
                print(f"{close} {open_} {high} {low} {int(vol)} {day}")
except: pass' 2>/dev/null
}

# --------------- 增量更新（每日收盘后运行） ---------------

update_single() {
  local code="$1"
  local cache_file="$CACHE_DIR/${code}.day"

  # 如果cache不存在、空文件、或少于30行 → 全量回补
  if [ ! -s "$cache_file" ] || [ "$(wc -l < "$cache_file")" -lt 30 ]; then
    local raw=$(fetch_60d "$code")
    [ -z "$raw" ] && return 1
    local parsed=$(parse_ohlcv "$raw")
    [ -z "$parsed" ] && return 1
    # 按日期排序写入（API返回从新到旧，需倒序保存）
    echo "$parsed" | sort -k3 -t' ' > "$cache_file"
    return 0
  fi

  # 正常增量：取cache中按日期排序的最后一行的日期
  local last_date=$(sort -k3 "$cache_file" | tail -1 | awk '{print $3}')
  local raw=$(fetch_60d "$code")
  [ -z "$raw" ] && return 1

  # 取API返回的最新一天（OHLCV格式）
  local latest=$(echo "$raw" | python3 -c '
import sys, json
try:
    txt = sys.stdin.read()
    if "=" in txt:
        txt = txt.split("=",1)[1].split(";")[0]
    data = json.loads(txt)
    d = data.get("data", {})
    keys = list(d.keys())
    if not keys:
        sys.exit(0)
    kline = d[keys[0]].get("qfqday") or d[keys[0]].get("day") or []
    if isinstance(kline, list) and len(kline) > 0:
        row = kline[0]
        close = row[2]
        open_ = row[1]
        high = row[3]
        low = row[4]
        vol = float(row[5]) * 100
        day = row[0]
        print(f"{close} {open_} {high} {low} {int(vol)} {day}")
except: pass' 2>/dev/null)

  local latest_date=$(echo "$latest" | awk '{print $6}')
  [ -z "$latest_date" ] && return 1

  # 去重：如果最新日期与cache最后一行相同，跳过
  if [ "$latest_date" = "$last_date" ]; then
    return 0  # 无新数据
  fi

  # 追加最新数据
  echo "$latest" >> "$cache_file"

  # 按日期排序后保留最近60行
  local tmp_file="${cache_file}.tmp"
  sort -k3 -t' ' "$cache_file" | tail -60 > "$tmp_file" && mv "$tmp_file" "$cache_file"
}

update_all() {
  echo "=== $(date '+%H:%M') 缓存增量更新 ==="
  local updated=0 skipped=0 failed=0
  local codes=$(get_all_codes | sort -u | grep -v '^$')
  local total=$(echo "$codes" | wc -l)
  local count=0

  for code in $codes; do
    [ -z "$code" ] && continue
    ((count++))
    if update_single "$code"; then
      # 检查文件是否真的变了
      ((updated++))
    else
      ((failed++))
    fi
    # 每20个休息300ms防限流
    [ $((count % 20)) -eq 0 ] && sleep 0.3
  done

  echo "  Updated: $updated  |  Skipped(no new): 0  |  Failed: $failed"
  echo "  Total cache files: $(ls "$CACHE_DIR"/*.day 2>/dev/null | wc -l)"
}

# --------------- 全量回补 ---------------

backfill_all() {
  echo "=== 全量回补60日OHLCV ==="
  local ok=0 fail=0 skip=0 count=0
  local codes=$(get_all_codes | sort -u | grep -v '^$')
  local total=$(echo "$codes" | wc -l)

  for code in $codes; do
    [ -z "$code" ] && continue
    ((count++))

    local cache_file="$CACHE_DIR/${code}.day"
    # 已有≥30行，跳过
    if [ -f "$cache_file" ] && [ "$(wc -l < "$cache_file")" -ge 30 ]; then
      ((skip++))
      continue
    fi

    local raw=$(fetch_60d "$code")
    [ -z "$raw" ] && { ((fail++)); continue; }
    local parsed=$(parse_ohlcv "$raw")
    [ -z "$parsed" ] && { ((fail++)); continue; }

    echo "$parsed" > "$cache_file"
    ((ok++))

    [ $((count % 10)) -eq 0 ] && echo "  Progress: $count/$total"
    [ $((count % 5)) -eq 0 ] && sleep 0.3
  done

  echo "  ✅ Backfilled: $ok  |  Skipped: $skip  |  Failed: $fail"
  echo "  Total: $(ls "$CACHE_DIR"/*.day 2>/dev/null | wc -l) files"
}

# --------------- 状态检查 ---------------

status() {
  echo "========== 缓存状态 =========="
  local ok=0 partial=0 empty=0 total=0
  for f in "$CACHE_DIR"/*.day; do
    [ ! -f "$f" ] && continue
    ((total++))
    local lines=$(wc -l < "$f")
    if [ "$lines" -ge 30 ]; then ((ok++))
    elif [ "$lines" -ge 1 ]; then ((partial++))
    else ((empty++)); fi
  done
  echo "  ✅ ≥30行: $ok"
  echo "  ⚠️  <30行: $partial"
  echo "  ❌ 空文件: $empty"
  echo "  📊 总计: $total"
  echo "=============================="

  # 检查有哪些文件行数不足
  if [ "$partial" -gt 0 ] || [ "$empty" -gt 0 ]; then
    echo ""
    echo "需要回补的文件："
    for f in "$CACHE_DIR"/*.day; do
      [ ! -f "$f" ] && continue
      local lines=$(wc -l < "$f")
      [ "$lines" -lt 30 ] && echo "  ❌ $(basename "$f" .day): ${lines}行"
    done
  fi
}

# --------------- 主入口 ---------------

case "${1:-status}" in
  update)
    DOW=$(date +%u)
    if [[ $DOW -ge 6 ]]; then
      echo "⏸️  非交易日（周$DOW），跳过日线缓存更新"
      exit 0
    fi
    update_all
    ;;
  status)  status ;;
  backfill) backfill_all ;;
  *)
    echo "用法: $0 {update|status|backfill}"
    echo "  update:   每日收盘后增量更新"
    echo "  status:   检查缓存状态"
    echo "  backfill: 全量回补60天"
    exit 1
    ;;
esac
