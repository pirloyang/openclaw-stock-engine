#!/bin/bash
# 缓存回填 V2 — 腾讯日K线API
# 用法: bash backfill_cache_v2.sh [--check-only]
CACHE_DIR="/root/.openclaw/workspace/stock-signals/cache"
WORKSPACE="/root/.openclaw/workspace"
mkdir -p "$CACHE_DIR"

code_to_prefix() {
  local code="$1"
  [[ $code == 6* || $code == 5* ]] && echo "sh" || echo "sz"
}

backfill_one() {
  local code="$1"
  local prefix=$(code_to_prefix "$code")
  local cache_file="$CACHE_DIR/${code}.day"
  
  # 腾讯日K线API
  local raw=$(curl -s --max-time 10 "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=${prefix}${code},day,,,120,qfq" 2>/dev/null)
  
  if [ -z "$raw" ]; then
    echo "  ⚠️ $code: API无响应"
    return 1
  fi
  
  # 用python3解析JSON并写入cache
  python3 -c "
import json, sys
raw = '''$raw'''
try:
    d = json.loads(raw)
    key = '${prefix}${code}'
    klines = d.get('data', {}).get(key, {}).get('day', []) or d.get('data', {}).get(key, {}).get('qfqday', [])
    if not klines:
        print(f'  ⚠️ ${code}: 无K线数据')
        sys.exit(1)
    
    with open('$cache_file', 'w') as f:
        for k in klines:
            # 格式: [date, open, close, high, low, volume]
            date = k[0]
            close = k[2]
            volume = k[5]
            f.write(f'{close} {volume} {date}\n')
    print(f'  ✅ ${code}: {len(klines)}条')
except Exception as e:
    print(f'  ❌ ${code}: {e}')
    sys.exit(2)
" 2>/dev/null
  return $?
}

CHECK_ONLY=0
[ "$1" = "--check-only" ] && CHECK_ONLY=1

echo "=== 缓存回填 V2 $(date '+%H:%M:%S') ==="

# 获取现有缓存文件列表
codes=$(ls "$CACHE_DIR"/*.day 2>/dev/null | sed 's/.*\///;s/\.day//' | sort -u)
total=$(echo "$codes" | wc -l)
echo "缓存标的: $total只"
echo ""

count=0
success=0
failed=0

for code in $codes; do
  count=$((count + 1))
  [ "$CHECK_ONLY" = "1" ] && continue
  
  result=$(backfill_one "$code")
  echo "$result"
  
  if echo "$result" | grep -q "✅"; then
    success=$((success + 1))
  else
    failed=$((failed + 1))
  fi
  
  # 小额延时，避免API限流
  [ $((count % 10)) -eq 0 ] && sleep 0.5
done

echo ""
echo "=== 结果: 成功${success} 失败${failed} ==="
