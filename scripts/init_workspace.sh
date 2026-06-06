#!/usr/bin/env bash
set -euo pipefail

# ──────────────────────────────────────────────
# init_workspace.sh — 工作区目录与初始数据初始化
# 用法：bash scripts/init_workspace.sh
# 幂等：可重复运行
# ──────────────────────────────────────────────

WS="${WORKSPACE_ROOT:-/root/.openclaw/workspace}"
cd "$WS"

echo "=== 创建运行时目录 ==="
mkdir -p reports                    # 每日报告产物
mkdir -p memory                     # AI 长短期记忆
mkdir -p sim_trading/reports        # 模拟交易日报
mkdir -p sim_trading/data           # 模拟交易状态
mkdir -p stock-signals/cache        # 行情缓存（每日追加）
mkdir -p data                       # 结算数据
mkdir -p /tmp/stock_alerts          # 信号引擎产物（运行时）

echo "✅ 目录就绪"

echo ""
echo "=== 校验关键数据文件 ==="
declare -A REQUIRED=(
    ["TOOLS.md"]="个人配置与持仓（git 不跟踪，首次部署需手动恢复）"
    ["data/investment_goals.json"]="投资目标里程碑"
    ["stock-signals/focus_watchlist.json"]="重点关注清单"
)

missing=0
for f in "${!REQUIRED[@]}"; do
    if [ -f "$f" ]; then
        echo "✅ $f"
    else
        echo "❌ $f  ← ${REQUIRED[$f]}"
        missing=$((missing + 1))
    fi
done

if [ $missing -gt 0 ]; then
    echo ""
    echo "⚠️  缺失 $missing 个关键文件。请从备份或参考：deploy/sample/ 恢复"
fi

echo ""
echo "=== 校验 cron jobs 状态 ==="
if [ -f "$HOME/.openclaw/cron/jobs.json" ]; then
    jobs_count=$(python3 -c "import json; print(len(json.load(open('$HOME/.openclaw/cron/jobs.json'))['jobs']))")
    enabled_count=$(python3 -c "import json; d=json.load(open('$HOME/.openclaw/cron/jobs.json')); print(sum(1 for j in d['jobs'] if j.get('enabled')))")
    echo "✅ 网关已有 cron jobs: 共 $jobs_count 个 / 启用 $enabled_count 个"
    echo "   若数量与 deploy/cron_manifest.json (期望 25) 不一致，跑："
    echo "   python3 deploy/cron_deploy.py --dry-run  # 查差异"
else
    echo "⚠️  网关 cron 尚未初始化，运行："
    echo "   python3 deploy/cron_deploy.py --dry-run && python3 deploy/cron_deploy.py --apply"
fi

echo ""
echo "✅ 初始化检查完成"