#!/bin/bash
# 模拟交易运行脚本
# 交易时间自动执行：09:35, 10:30, 11:25, 13:35, 14:30

WORKSPACE="/root/.openclaw/workspace/sim_trading"
LOG="$WORKSPACE/logs/sim_$(date +%Y%m%d).log"

mkdir -p "$WORKSPACE/logs" "$WORKSPACE/reports"

echo "=== $(date '+%H:%M:%S') 模拟交易会话 ===" >> "$LOG"
python3 "$WORKSPACE/sim_engine.py" >> "$LOG" 2>&1
echo "" >> "$LOG"

# 收盘后生成日报 (15:10)
if [ "$(date +%H%M)" -ge "1510" ] && [ "$(date +%H%M)" -lt "1520" ]; then
    echo "=== $(date '+%H:%M:%S') 生成日报 ===" >> "$LOG"
    python3 "$WORKSPACE/sim_engine.py" report >> "$LOG" 2>&1
    echo "" >> "$LOG"
fi
