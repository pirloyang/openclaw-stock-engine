#!/usr/bin/env bash
set -euo pipefail

# ──────────────────────────────────────────────
# install_deps.sh — 股票量化系统系统依赖安装
# 运行环境：Ubuntu 22.04+ / Debian 12+
# 用法：bash scripts/install_deps.sh
# 建议先更新系统：apt update && apt upgrade -y
# ──────────────────────────────────────────────

echo "=== 安装系统依赖 ==="

# ── 基础工具 ──
apt-get install -y --no-install-recommends \
    curl wget git jq \
    tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-chi-tra \
    python3 python3-pip python3-venv

# ── Playwright（浏览器自动化） ──
# OpenClaw 浏览器控制依赖 Chrome/Chromium
if ! command -v playwright >/dev/null 2>&1; then
    echo "--- 安装 playwright ---"
    pip3 install playwright
    playwright install chromium
    playwright install-deps chromium
fi

# ── pnpm（Node 包管理器，OpenClaw 官方推荐） ──
if ! command -v pnpm >/dev/null 2>&1; then
    echo "--- 安装 pnpm ---"
    npm install -g pnpm
fi

# ── OpenClaw CLI（安装 OpenClaw 后自带，确认可用） ──
if ! command -v openclaw >/dev/null 2>&1; then
    echo "⚠️  openclaw CLI 未安装在同一 PATH 中"
    echo "   请参考 https://docs.openclaw.ai 安装 OpenClaw"
fi

echo ""
echo "=== 安装 Python 依赖 ==="
pip3 install -r requirements.txt

echo ""
echo "=== 创建必需目录 ==="
mkdir -p /tmp/stock_alerts
mkdir -p stock-signals
mkdir -p sim_trading/reports
mkdir -p reports
mkdir -p data
mkdir -p deploy

echo ""
echo "✅ 所有依赖安装完成"
echo ""
echo "后续手动步骤（初次部署）："
echo "  1. cp deploy/.env.example deploy/.env && vim deploy/.env  # 填 API Key"
echo "  2. python3 deploy/cron_deploy.py --dry-run                # 预览 cron"
echo "  3. python3 deploy/cron_deploy.py --apply                  # 部署 cron"
echo "  4. openclaw gateway restart                               # 重启（如有必要）"