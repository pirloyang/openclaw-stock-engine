#!/bin/bash
# ============================================================
# 一键部署脚本
# 用法: bash setup.sh
# ============================================================
set -e

WORKSPACE="/root/.openclaw/workspace"

echo "🚀 OpenClaw Stock Engine V4.0 部署开始"
echo ""

# 1. 检查 OpenClaw Gateway
echo "📦 检查 OpenClaw Gateway..."
if ! command -v openclaw &> /dev/null; then
    echo "⚠️ 未检测到 openclaw，请先安装 OpenClaw Gateway"
    echo "   curl -fsSL https://docs.openclaw.ai/install.sh | bash"
    exit 1
fi
echo "✅ openclaw 已安装"

# 2. 安装 Python 依赖
echo "📦 安装 Python 依赖..."
pip install baostock -q 2>/dev/null || echo "⚠️ baostock 安装失败（可能需要单独处理）"

# 3. 安装系统依赖
echo "📦 安装系统依赖..."
if command -v apt-get &> /dev/null; then
    apt-get install -y -qq jq curl bc 2>/dev/null || true
elif command -v yum &> /dev/null; then
    yum install -y -q jq curl bc 2>/dev/null || true
fi

# 4. 创建必要目录
echo "📁 创建目录结构..."
mkdir -p "$WORKSPACE/reports"
mkdir -p "$WORKSPACE/stock-signals/cache"
mkdir -p /tmp/stock_alerts

# 5. 初始化日线缓存
echo "📊 初始化日线缓存（首次部署需要约2-3分钟）..."
if [ -f "$WORKSPACE/stock-signals/backfill_cache.sh" ]; then
    bash "$WORKSPACE/stock-signals/backfill_cache.sh"
    echo "✅ 日线缓存初始化完成"
else
    echo "⚠️ backfill_cache.sh 未找到"
fi

# 6. 配置个人文件
echo "📝 配置个人文件..."
for f in TOOLS.md AGENTS.md SOUL.md USER.md IDENTITY.md; do
    if [ ! -f "$WORKSPACE/$f" ]; then
        if [ -f "$WORKSPACE/examples/$f.example" ]; then
            cp "$WORKSPACE/examples/$f.example" "$WORKSPACE/$f"
            echo "  ✅ 已从示例复制: $f"
        fi
    else
        echo "  ⏭️ 已存在: $f"
    fi
done

# 7. 导入 Cron
echo "⏰ 导入 Cron 定时任务..."
if [ -f "$WORKSPACE/crons/jobs.json" ]; then
    cp "$WORKSPACE/crons/jobs.json" /root/.openclaw/cron/jobs.json
    echo "✅ Cron 配置已导入"
else
    echo "⚠️ crons/jobs.json 未找到"
fi

# 8. 重启 Gateway
echo "🔁 重启 OpenClaw Gateway..."
openclaw gateway restart 2>/dev/null || echo "⚠️ 重启失败，请手动重启"

echo ""
echo "========================================="
echo "✅ OpenClaw Stock Engine V4.0 部署完成！"
echo "========================================="
echo ""
echo "下一步："
echo "  1. 编辑 TOOLS.md 填入你的持仓和自选"
echo "  2. 编辑 AGENTS.md 自定义行为规则"
echo "  3. 等待下一个交易日开盘，Cron 自动运行"
echo ""
