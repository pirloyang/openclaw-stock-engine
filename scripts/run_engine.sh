#!/bin/bash
# 引擎信号采集包装器（带进程锁）
LOCKFILE="/tmp/engine_run.lock"
OUTPUT_DIR="/tmp/stock_alerts"
WORKSPACE="/root/.openclaw/workspace"

# 互斥锁：如果已有引擎在跑，跳过本轮
exec 9>"$LOCKFILE"
if ! flock -n 9; then
    echo "⚠️ 引擎已在运行，跳过本轮"
    exit 0
fi

ENGINE="$WORKSPACE/stock-signals/engine.sh"
mkdir -p "$OUTPUT_DIR"

# 跑引擎
result=$("$ENGINE" 2>/dev/null)
signal_file=$(echo "$result" | grep '^signal_file=' | cut -d= -f2)

if [ -f "$signal_file" ]; then
    python3 -c "import json; json.load(open('$signal_file'))" 2>/dev/null
    if [ $? -eq 0 ]; then
        cp "$signal_file" "$OUTPUT_DIR/engine_signals.json"
        echo "✅ 引擎信号已写入"
    else
        echo "⚠️ 信号JSON不完整"
        rm -f "$signal_file"
    fi
fi

# ---------- 收盘价校准（仅在15:05后执行） ----------
# 收盘后用gtimg实时API取正式收盘价，覆盖盘中最后一次快照
if [ "$(date +%H%M)" -ge 1505 ]; then
    SIGNALS_FILE="$OUTPUT_DIR/engine_signals.json"
    if [ -f "$SIGNALS_FILE" ]; then
        echo "⏳ 收盘价校准中..."
        python3 << 'CALPY'
import json, subprocess

with open('/tmp/stock_alerts/engine_signals.json') as f:
    signals = json.load(f)

codes = [s['code'] for s in signals if s.get('code') and s['code'] not in ('000001','399001','399006','000688')]
print(f"  共{len(codes)}只标的待校准")

updated = 0
for i in range(0, len(codes), 30):
    batch = codes[i:i+30]
    qs = []
    for c in batch:
        prefix = "sh" if c.startswith(('6','5','9')) else "sz"
        qs.append(f"{prefix}{c}")
    query = ",".join(qs)
    try:
        r = subprocess.run(['curl', '-s', '--max-time', '10',
            f'https://qt.gtimg.cn/q={query}'],
            capture_output=True, timeout=10)
        raw = r.stdout.decode('gbk', errors='replace')
        for line in raw.strip().split('\n'):
            parts = line.split('~')
            if len(parts) < 5: continue
            code_full = parts[2] if len(parts) > 2 else ''
            try:
                close_price = float(parts[3])
            except (ValueError, IndexError):
                continue
            for s in signals:
                if s['code'] == code_full:
                    old_price = s.get('price', 0)
                    s['price'] = close_price
                    if abs(old_price - close_price) > 0.01:
                        updated += 1
                    break
    except Exception as e:
        print(f"  batch {i//30+1} error: {e}")
        continue

with open('/tmp/stock_alerts/engine_signals.json', 'w') as f:
    json.dump(signals, f, ensure_ascii=False)

print(f"📊 收盘校准完成: {updated}/{len(codes)} 只标的已更新收盘价")
CALPY
    fi
fi

flock -u 9
