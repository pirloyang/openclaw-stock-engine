#!/bin/bash
# compute_concept_benchmarks.sh — 概念板块基准计算器
# 独立于engine.sh运行，直接从gtimg API拉取概念成分股实时行情
# 输出: /tmp/concept_benchmarks.json
# ==========================================================

CONCEPT_MAP="/root/.openclaw/workspace/stock-signals/concept_map.json"
BENCHMARK_FILE="/tmp/concept_benchmarks.json"
WORKSPACE="/root/.openclaw/workspace"

compute_benchmarks() {
  # 收集所有概念成分股的代码
  local codes=$(python3 -c "
import json
with open('$CONCEPT_MAP') as f:
    data = json.load(f)
all_stocks = set()
for cname, cdef in data['concepts'].items():
    for s in cdef.get('sampled_stocks', []):
        all_stocks.add(s)
for code in sorted(all_stocks):
    print(code)
" 2>/dev/null)

  # 构建gtimg查询字符串（每批最多50只）
  local batch="" count=0 raw=""
  for code in $codes; do
    [ -z "$code" ] && continue
    if [[ $code == 6* || $code == "000001" ]]; then
      batch="${batch}sh${code},"
    else
      batch="${batch}sz${code},"
    fi
    ((count++))
    if [ "$count" -ge 50 ]; then
      raw="${raw}$(curl -s --max-time 8 "https://qt.gtimg.cn/q=${batch%,}" 2>/dev/null | iconv -f GBK -t UTF-8 2>/dev/null)"
      batch=""; count=0
    fi
    sleep 0.1  # 缓和API请求
  done
  [ -n "$batch" ] && raw="${raw}$(curl -s --max-time 8 "https://qt.gtimg.cn/q=${batch%,}" 2>/dev/null | iconv -f GBK -t UTF-8 2>/dev/null)"

  # 解析涨跌幅并计算概念基准
  echo "$raw" | python3 -c "
import sys, json

with open('$CONCEPT_MAP') as f:
    cmap = json.load(f)

concepts = cmap.get('concepts', {})

# 提取所有股票的涨跌幅
stocks = {}
for line in sys.stdin:
    if '~' not in line: continue
    parts = line.split('~')
    if len(parts) < 33: continue
    code = parts[2]  # 股票代码在parts[2]
    change_str = parts[32]  # 涨跌幅在parts[32]
    if change_str and change_str != '':
        try:
            stocks[code] = float(change_str.replace('%', '').strip())
        except:
            pass

# 计算每个概念的等权基准
result = {}
for cname, cdef in concepts.items():
    sample = cdef.get('sampled_stocks', [])
    changes = []
    for s in sample:
        if s in stocks:
            changes.append(stocks[s])
    if changes:
        avg = sum(changes) / len(changes)
        result[cname] = {
            'name': cdef.get('name', cname),
            'avg_change': round(avg, 2),
            'stocks_used': len(changes),
            'stocks_total': len(sample),
            'stocks_detail': {s: stocks.get(s, None) for s in sample if s in stocks}
        }

with open('$BENCHMARK_FILE', 'w') as f:
    json.dump(result, f)

# 同时输出摘要到stderr
print(f'=== 概念板块基准 ===', file=sys.stderr)
for cname, info in result.items():
    print(f'{info[\"name\"]}: {info[\"avg_change\"]}% (用了{info[\"stocks_used\"]}/{info[\"stocks_total\"]}只)', file=sys.stderr)
" 2>&1
}

case "${1:-compute}" in
  compute)
    compute_benchmarks
    ;;
  show)
    if [ -f "$BENCHMARK_FILE" ]; then
      python3 -c "
import json
with open('$BENCHMARK_FILE') as f:
    data = json.load(f)
for name, info in data.items():
    print(f'{info[\"name\"]}: {info[\"avg_change\"]}% (用了{info[\"stocks_used\"]}/{info[\"stocks_total\"]}只)')
"
    else
      echo "基准文件不存在，请先运行 compute"
    fi
    ;;
  *)
    echo "用法: $0 {compute|show}"
    ;;
esac
