#!/bin/bash
# ============================================================
# V4.1 一键备份脚本
# 备份时间: $(date +%Y%m%d_%H%M%S)
# V4.0 → V4.1 核心变更: 
#   修复数据流水线断裂 + 去重状态持久化 + cron架构补全
# ============================================================
set -e

WORKSPACE="/root/.openclaw/workspace"
BACKUP_DIR="${WORKSPACE}/backups/v4.1_$(date +%Y%m%d_%H%M%S)"

echo "📦 V4.1 系统备份开始..."
echo "   目标: $BACKUP_DIR"
echo ""

mkdir -p "$BACKUP_DIR"

# === 1. 核心配置与文档 ===
echo "📋 核心配置文件..."
cp "$WORKSPACE"/*.md "$BACKUP_DIR/" 2>/dev/null || true
cp "$WORKSPACE"/*.json "$BACKUP_DIR/" 2>/dev/null || true

# === 2. 报告模板与机制文档 ===
echo "📋 报告模板与机制文档..."
mkdir -p "$BACKUP_DIR/docs"
cp -r "$WORKSPACE"/docs/* "$BACKUP_DIR/docs/" 2>/dev/null || true

# === 3. 报告归档 ===
echo "📋 历史报告..."
mkdir -p "$BACKUP_DIR/reports"
cp -r "$WORKSPACE"/reports/* "$BACKUP_DIR/reports/" 2>/dev/null || true

# === 4. data 目录（持久化状态）===
echo "📋 持久化数据..."
mkdir -p "$BACKUP_DIR/data"
cp -r "$WORKSPACE"/data/* "$BACKUP_DIR/data/" 2>/dev/null || true

# === 5. 脚本目录 ===
echo "📋 脚本..."
mkdir -p "$BACKUP_DIR/scripts"
cp "$WORKSPACE"/scripts/*.sh "$BACKUP_DIR/scripts/" 2>/dev/null || true
cp "$WORKSPACE"/scripts/*.py "$BACKUP_DIR/scripts/" 2>/dev/null || true
cp "$WORKSPACE"/scripts/*.json "$BACKUP_DIR/scripts/" 2>/dev/null || true

# === 6. 信号引擎 ===
echo "📋 信号引擎..."
mkdir -p "$BACKUP_DIR/stock-signals"
cp "$WORKSPACE"/stock-signals/*.sh "$BACKUP_DIR/stock-signals/" 2>/dev/null || true
cp "$WORKSPACE"/stock-signals/*.py "$BACKUP_DIR/stock-signals/" 2>/dev/null || true
cp "$WORKSPACE"/stock-signals/*.json "$BACKUP_DIR/stock-signals/" 2>/dev/null || true
cp "$WORKSPACE"/stock-signals/*.md "$BACKUP_DIR/stock-signals/" 2>/dev/null || true
mkdir -p "$BACKUP_DIR/stock-signals/rules"
cp -r "$WORKSPACE"/stock-signals/rules/* "$BACKUP_DIR/stock-signals/rules/" 2>/dev/null || true

# 日线缓存
echo "   日线缓存..."
mkdir -p "$BACKUP_DIR/stock-signals/cache"
cp -r "$WORKSPACE"/stock-signals/cache/*.day "$BACKUP_DIR/stock-signals/cache/" 2>/dev/null || true
CACHE_COUNT=$(find "$BACKUP_DIR/stock-signals/cache" -name "*.day" 2>/dev/null | wc -l)
echo "   日线缓存: ${CACHE_COUNT} 个文件"

# === 7. 模拟交易系统 ===
echo "📋 模拟交易..."
mkdir -p "$BACKUP_DIR/sim_trading"
mkdir -p "$BACKUP_DIR/sim_trading/data"
mkdir -p "$BACKUP_DIR/sim_trading/logs"
mkdir -p "$BACKUP_DIR/sim_trading/reports"
cp -r "$WORKSPACE"/sim_trading/*.sh "$BACKUP_DIR/sim_trading/" 2>/dev/null || true
cp -r "$WORKSPACE"/sim_trading/*.py "$BACKUP_DIR/sim_trading/" 2>/dev/null || true
cp -r "$WORKSPACE"/sim_trading/*.json "$BACKUP_DIR/sim_trading/" 2>/dev/null || true
cp -r "$WORKSPACE"/sim_trading/data/* "$BACKUP_DIR/sim_trading/data/" 2>/dev/null || true
cp -r "$WORKSPACE"/sim_trading/logs/* "$BACKUP_DIR/sim_trading/logs/" 2>/dev/null || true
cp -r "$WORKSPACE"/sim_trading/reports/* "$BACKUP_DIR/sim_trading/reports/" 2>/dev/null || true

# === 8. Cron 配置 ===
echo "📋 Cron 定时任务配置..."
mkdir -p "$BACKUP_DIR/crons"
cp /root/.openclaw/cron/jobs.json "$BACKUP_DIR/crons/" 2>/dev/null || true
cp /root/.openclaw/cron/jobs-state.json "$BACKUP_DIR/crons/" 2>/dev/null || true

# === 9. memory 投资项目管理 ===
echo "📋 投资项目管理看板..."
mkdir -p "$BACKUP_DIR/memory"
cp "$WORKSPACE"/memory/investment_project.md "$BACKUP_DIR/memory/" 2>/dev/null || true
cp "$WORKSPACE"/memory/*.json "$BACKUP_DIR/memory/" 2>/dev/null || true

# === 10. 生成清单 ===
echo ""
echo "📝 生成备份清单..."
cat > "$BACKUP_DIR/backup_manifest.md" << 'MANIFEST_EOF'
# V4.1 备份清单

## V4.0 → V4.1 核心变更

| 变更项 | 文件 | 说明 |
|:-------|:-----|:-----|
| 🆕 数据流水线 | scripts/pipeline.sh | 统一数据生产：engine→layer_monitor→signals_summary→sector_fund→industry_chain |
| 🔧 去重持久化 | stock-signals/signal_dedup.py | STATE_FILE 从 /tmp 迁至 workspace/data/ |
| 🆕 持久化数据 | data/ | signal_state.json 等持久化状态文件 |
| 🔧 Cron补全 | crons/jobs.json | 新增数据流水线cron + 收盘快照改用pipeline.sh |
| 🔧 TOOLS更新 | TOOLS.md | 宁德时代入池 + 多标的更新 |
| 🔧 超时修复 | crons/jobs.json | 周末风口研报 timeout 300→600s |

## 备份内容

```
backups/v4.1_YYYYMMDD_HHMMSS/
├── *.md                  # 核心配置 (AGENTS, SOUL, TOOLS, MEMORY等)
├── docs/                 # 报告模板与机制文档
├── reports/              # 历史研报
├── data/                 # 持久化状态数据
├── scripts/              # 所有脚本 (.sh + .py + .json)
├── stock-signals/        # 信号引擎 + 规则 + 日线缓存
├── sim_trading/          # 模拟交易系统
├── crons/                # Cron 定时任务配置
└── memory/               # 投资项目管理看板
```

## 关键架构状态 (V4.1)

- **信号引擎**: V3.0 (29规则 + 概念相对强度 + 板块资金)
- **Cron**: 16个活跃任务 (日报告7个 + 交易监控3个 + 数据流水线1个 + 模拟1个 + 彩票2个 + 周/月2个)
- **监控层级**: L1(大盘) → L2(持仓) → L3(重点关注) → L4(ETF/概念) → L5(自选观察) → L6(热点)
- **数据流**: pipeline cron → engine.sh → layer_monitor.py → L1-L6文件 → 监控cron读取
MANIFEST_EOF

# 一键恢复脚本
cat > "$BACKUP_DIR/restore.sh" << 'RESTORE_EOF'
#!/bin/bash
# V4.1 一键恢复脚本
set -e
SRC="$(dirname "$(readlink -f "$0")")"
WS="/root/.openclaw/workspace"
echo "🔄 从 $SRC 恢复 V4.1 系统到 $WS"
cp -r "$SRC"/*.md "$WS/"
cp -r "$SRC"/docs/* "$WS/docs/"
cp -r "$SRC"/reports/* "$WS/reports/"
cp -r "$SRC"/data/* "$WS/data/"
cp -r "$SRC"/scripts/* "$WS/scripts/"
cp -r "$SRC"/stock-signals/* "$WS/stock-signals/"
cp -r "$SRC"/sim_trading/* "$WS/sim_trading/"
cp -r "$SRC"/memory/* "$WS/memory/"
cp "$SRC"/crons/jobs.json /root/.openclaw/cron/
echo "✅ V4.1 恢复完成，请重启 Gateway"
RESTORE_EOF
chmod +x "$BACKUP_DIR/restore.sh"

# 计算大小
BACKUP_SIZE=$(du -sh "$BACKUP_DIR" | cut -f1)
FILE_COUNT=$(find "$BACKUP_DIR" -type f | wc -l)

echo ""
echo "============================================"
echo "✅ V4.1 备份完成"
echo "   路径: $BACKUP_DIR"
echo "   大小: $BACKUP_SIZE"
echo "   文件: $FILE_COUNT 个"
echo "   恢复: bash $BACKUP_DIR/restore.sh"
echo "============================================"
