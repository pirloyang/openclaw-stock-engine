#!/bin/bash
# ============================================================
# 数据流水线 — 盘中分层数据生产器
# 输入: 无（从 gtimg API 拉数据）
# 输出: /tmp/stock_alerts/ 下的L1-L6分层文件+信号摘要
# 耗时: 约15-30秒
# ============================================================
set -e

WORKSPACE="/root/.openclaw/workspace"
ALERT_DIR="/tmp/stock_alerts"
LOCKFILE="/tmp/pipeline.lock"

# 互斥锁
exec 9>"$LOCKFILE"
if ! flock -n 9; then
    echo "⚠️ pipeline已在运行，跳过"
    exit 0
fi

mkdir -p "$ALERT_DIR"
START_TS=$(date +%s)

# 交易日判断：非交易日只做只读验证，不写产物
DOW=$(date +%u)  # 1=Mon..5=Fri, 6=Sat, 7=Sun
if [[ $DOW -ge 6 ]]; then
    echo "⏸️  [$(date '+%H:%M:%S')] 非交易日（周$DOW），跳过数据生产"
    exit 0
fi

echo "🔄 [$(date '+%H:%M:%S')] 数据流水线启动..."

# ===== 1. 引擎信号采集 =====
echo "  📡 运行信号引擎..."
RESULT=$(bash "$WORKSPACE/stock-signals/engine.sh" 2>/dev/null)
SIGNAL_FILE=$(echo "$RESULT" | grep '^signal_file=' | cut -d= -f2)

if [ -f "$SIGNAL_FILE" ] && [ -s "$SIGNAL_FILE" ]; then
    python3 -c "import json; json.load(open('$SIGNAL_FILE'))" 2>/dev/null && {
        cp "$SIGNAL_FILE" "$ALERT_DIR/engine_signals.json"
        echo "  ✅ 引擎信号: $(wc -c < "$ALERT_DIR/engine_signals.json") bytes"
    } || echo "  ⚠️ 信号JSON不完整，使用上次缓存"
else
    echo "  ⚠️ 引擎无输出，使用上次缓存"
fi

# ===== 2. 分层监控采集 (L1-L6) =====
if [ -f "$ALERT_DIR/engine_signals.json" ]; then
    echo "  📊 运行分层采集..."
    python3 "$WORKSPACE/scripts/layer_monitor.py" 2>/dev/null && {
        for f in L1_market L2_holdings L3_focus L4_etf_concept L5_watchlist L6_hot_alerts urgent; do
            if [ -f "$ALERT_DIR/${f}.txt" ]; then
                echo "     ✅ $f: $(wc -l < "$ALERT_DIR/${f}.txt") lines"
            fi
        done
    } || echo "  ⚠️ layer_monitor 执行失败"
else
    echo "  ⚠️ 跳过分层采集: engine_signals.json 不存在"
fi

# ===== 3. 信号摘要压缩 =====
if [ -f "$ALERT_DIR/engine_signals.json" ]; then
    echo "  📝 运行信号摘要..."
    python3 "$WORKSPACE/scripts/signals_summary.py" 2>/dev/null && {
        if [ -f "$ALERT_DIR/signals_summary.json" ]; then
            echo "     ✅ signals_summary: $(wc -c < "$ALERT_DIR/signals_summary.json") bytes"
        fi
    } || echo "  ⚠️ signals_summary 执行失败"
fi

# ===== 4. 板块资金流向 (V2.3) =====
if [ -f "$ALERT_DIR/engine_signals.json" ]; then
    echo "  💰 运行板块资金..."
    python3 "$WORKSPACE/scripts/sector_fund_flow.py" 2>/dev/null && {
        if [ -f "$ALERT_DIR/sector_flow.json" ]; then
            echo "     ✅ sector_flow: $(wc -c < "$ALERT_DIR/sector_flow.json") bytes"
        fi
    } || echo "  ⚠️ sector_fund_flow 执行失败"
fi

# ===== 5. 产业链传导 =====
if [ -f "$ALERT_DIR/sector_flow.json" ]; then
    echo "  🔗 运行产业链分析..."
    python3 "$WORKSPACE/scripts/industry_chain.py" 2>/dev/null && {
        if [ -f "$ALERT_DIR/chain_alerts.json" ]; then
            echo "     ✅ chain_alerts: $(wc -c < "$ALERT_DIR/chain_alerts.json") bytes"
        fi
    } || echo "  ⚠️ industry_chain 执行失败"
fi

# ===== 6. 净值快照刷新（仅收盘后16:00轮次）=====
HOUR=$(date +%H)
if [ "$HOUR" -ge 15 ]; then
    echo "  📊 刷新净值快照..."
    python3 "$WORKSPACE/scripts/portfolio_engine.py" 2>/dev/null && {
        if [ -f "$WORKSPACE/data/portfolio_snapshot.json" ]; then
            echo "     ✅ snapshot: $(wc -c < "$WORKSPACE/data/portfolio_snapshot.json") bytes"
        fi
    } || echo "  ⚠️ portfolio_engine 执行失败"
fi

ELAPSED=$(($(date +%s) - START_TS))
echo "✅ [$(date '+%H:%M:%S')] 流水线完成 (${ELAPSED}s)"
flock -u 9
