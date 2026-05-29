#!/bin/bash
# ==========================================================
# 缓存回补脚本 — 全量拉取60个交易日的OHLCV数据
# 用法: bash backfill_cache.sh [--check-only]
# --check-only: 只检查哪些文件数据不足，不做回补
# ==========================================================

CACHE_DIR="/root/.openclaw/workspace/stock-signals/cache"
WORKSPACE="/root/.openclaw/workspace"
mkdir -p "$CACHE_DIR"

# 获取全池代码（与engine.sh保持一致）
get_all_codes() {
  # 指数
  printf "000001\n399001\n399006\n"
  bash "$WORKSPACE/scripts/tools.sh" holdings 2>/dev/null | awk '{print $1}'
  bash "$WORKSPACE/scripts/tools.sh" history  2>/dev/null | awk '{print $1}'
  # 一行一个代码（tr把空格和换行都转成换行，删空行）
  cat <<'CODES' | tr ' \t' '\n' | grep -v '^$'
516640
159667
159858
159928
512400
688008
300308
300394
002230
300750
300502
600522
300456
002281
300620
601138
000977
300476
000034
002837
300499
301018
300738
300383
001309
300475
002119
300302
300661
688798
300223
603881
300857
000032
002335
600602
600118
002025
300045
688568
300762
600343
300455
688523
301306
002465
600391
600592
301005
000901
002682
600151
000551
300265
002361
003009
600345
002151
CODES
}

# 从新浪API拉取60天OHLCV
# 统一前缀映射 — 与gtimg/qt.gtimg.cn保持一致
code_to_prefix() {
  local code="$1"
  # 沪市(6开头), ETF沪市(5开头), 上证指数(000001)
  [[ $code == 6* || $code == 5* || $code == "000001" ]] && { echo "sh"; return; }
  # 其它（3创业板, 0深市, 1深市, 2, 4）
  echo "sz"
}

fetch_60d() {
  local code="$1" pfx="$(code_to_prefix "$code")"
  curl -s --max-time 10 \
    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=${pfx}${code},day,,,60,qfq" 2>/dev/null
}

check_status() {
  echo "========== 缓存健康状况 =========="
  local total=0 ok=0 partial=0 empty=0
  for f in "$CACHE_DIR"/*.day; do
    [ ! -f "$f" ] && continue
    local code=$(basename "$f" .day)
    local lines=$(wc -l < "$f")
    ((total++))
    if [ "$lines" -ge 30 ]; then
      ((ok++))
    elif [ "$lines" -ge 5 ]; then
      ((partial++))
      echo "  ⚠️ $code: ${lines}行（< 30，勉强可用）"
    elif [ "$lines" -ge 1 ]; then
      ((partial++))
      echo "  ❌ $code: ${lines}行（< 5，量比失效）"
    else
      ((empty++))
      echo "  ❌ $code: 0行（空文件）"
    fi
  done

  # 检查是否有代码在监控池但未建立cache文件
  local missing=0
  while read code; do
    [ -z "$code" ] && continue
    [ ! -f "$CACHE_DIR/${code}.day" ] && {
      echo "  ❌ $code: cache文件不存在"
      ((missing++))
    }
  done < <(get_all_codes)

  echo ""
  echo "总计: ${total}个cache文件"
  echo "  ✅ 优质(≥30行): ${ok}"
  echo "  ⚠️ 不足(<30行): ${partial}"
  echo "  ❌ 空文件: ${empty}"
  echo "  ❌ 缺失: ${missing}"
  echo "================================"
  [ "$partial" -gt 0 ] || [ "$empty" -gt 0 ] || [ "$missing" -gt 0 ] && return 1
  return 0
}

backfill_all() {
  echo "========== 开始全量回补60日OHLCV =========="
  local ok=0 fail=0 skip=0 code_count=0 total=$(get_all_codes | wc -l)
  local batch=0

  while read code; do
    [ -z "$code" ] && continue
    ((code_count++))

    local cache_file="$CACHE_DIR/${code}.day"
    # 已有≥30行，跳过
    if [ -f "$cache_file" ] && [ "$(wc -l < "$cache_file")" -ge 30 ]; then
      # 简单去重：检查最后3行是否与回补数据一致
      ((skip++))
      continue
    fi

    # 拉取60天数据
    local raw=$(fetch_60d "$code")
    [ -z "$raw" ] && { echo "  ❌ $code: API无返回"; ((fail++)); continue; }

    local parsed=$(echo "$raw" | python3 -c "
import sys, json
raw = sys.stdin.read()
if '=' in raw:
    raw = raw.split('=')[1].split(';')[0]
try:
    data = json.loads(raw)
    d = data.get('data', {})
    keys = list(d.keys())
    kline = d[keys[0]].get('qfqday') or d[keys[0]].get('day') or []
    for row in kline:
        if len(row) >= 6:
            close = row[2]
            vol = float(row[5]) * 100
            day = row[0]
            print(f'{close} {int(vol)} {day}')
except Exception as e:
    sys.stderr.write(str(e))
" 2>/dev/null)

    [ -z "$parsed" ] && { echo "  ❌ $code: 解析失败"; ((fail++)); continue; }

    echo "$parsed" > "$cache_file"
    echo "  ✅ $code: 重建$(wc -l < "$cache_file")行"
    ((ok++))

    # 每10个代码输出一次进度
    ((code_count % 10 == 0)) && echo "--- 进度: $code_count/$total ---"

    # 缓和API速率
    [ $((code_count % 5)) -eq 0 ] && sleep 0.3
  done < <(get_all_codes)

  echo ""
  echo "========== 回补完成 =========="
  echo "  ✅ 重建: $ok"
  echo "  ❌ 失败: $fail"
  echo "  ➖ 跳过(已有): $skip"
  echo "================================"
}

case "${1:-}" in
  --check-only)
    check_status
    ;;
  --check)
    check_status
    ;;
  *)
    check_status
    echo ""
    echo "按回车开始回补，Ctrl+C取消..."
    read
    backfill_all
    echo ""
    check_status
    ;;
esac
