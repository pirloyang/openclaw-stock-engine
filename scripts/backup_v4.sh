#!/bin/bash
# ============================================================
# V4.0 一键备份脚本
# 版本: v4.0_20260529_1643
# 描述: 完整备份当前股票量化系统（含今日 6 项修复）
# ============================================================
set -e

WORKSPACE="/root/.openclaw/workspace"
BACKUP_DIR="${WORKSPACE}/backups/v4.0_20260529_1643"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

echo "📦 V4.0 系统备份开始..."
echo "   目标: $BACKUP_DIR"
echo ""

mkdir -p "$BACKUP_DIR"

# === 1. 核心配置与文档 ===
echo "📋 核心配置文件..."
cp -r "$WORKSPACE"/*.md "$BACKUP_DIR/"
cp -r "$WORKSPACE"/*.json "$BACKUP_DIR/" 2>/dev/null || true
cp -r "$WORKSPACE"/engine.sh "$BACKUP_DIR/"
cp -r "$WORKSPACE"/info-sources.md "$BACKUP_DIR/" 2>/dev/null || true
cp -r "$WORKSPACE"/ROLES.md "$BACKUP_DIR/" 2>/dev/null || true
cp -r "$WORKSPACE"/PORTFOLIO.md "$BACKUP_DIR/" 2>/dev/null || true

# === 2. 报告模板与机制文档 ===
echo "📋 报告模板与机制文档..."
mkdir -p "$BACKUP_DIR/docs"
cp -r "$WORKSPACE"/docs/* "$BACKUP_DIR/docs/"

# === 3. 脚本目录 ===
echo "📋 脚本..."
mkdir -p "$BACKUP_DIR/scripts"
cp -r "$WORKSPACE"/scripts/*.sh "$BACKUP_DIR/scripts/"
cp -r "$WORKSPACE"/scripts/*.py "$BACKUP_DIR/scripts/"
cp -r "$WORKSPACE"/scripts/*.json "$BACKUP_DIR/scripts/" 2>/dev/null || true

# === 4. 信号引擎（含日线缓存） ===
echo "📋 信号引擎（含日线缓存 ~120 只标的）..."
mkdir -p "$BACKUP_DIR/stock-signals"
cp -r "$WORKSPACE"/stock-signals/*.sh "$BACKUP_DIR/stock-signals/"
cp -r "$WORKSPACE"/stock-signals/*.py "$BACKUP_DIR/stock-signals/"
cp -r "$WORKSPACE"/stock-signals/*.json "$BACKUP_DIR/stock-signals/" 2>/dev/null || true
cp -r "$WORKSPACE"/stock-signals/*.md "$BACKUP_DIR/stock-signals/" 2>/dev/null || true
mkdir -p "$BACKUP_DIR/stock-signals/rules"
cp -r "$WORKSPACE"/stock-signals/rules/* "$BACKUP_DIR/stock-signals/rules/"

# 日线缓存（~120 文件，重要！）
echo "   日线缓存..."
mkdir -p "$BACKUP_DIR/stock-signals/cache"
cp -r "$WORKSPACE"/stock-signals/cache/*.day "$BACKUP_DIR/stock-signals/cache/" 2>/dev/null || true
CACHE_COUNT=$(find "$BACKUP_DIR/stock-signals/cache" -name "*.day" | wc -l)
echo "   日线缓存: ${CACHE_COUNT} 个文件"

# === 5. 模拟交易系统 ===
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

# === 6. Cron 配置 ===
echo "📋 Cron 定时任务配置..."
mkdir -p "$BACKUP_DIR/crons"
cp -r /root/.openclaw/cron/jobs.json "$BACKUP_DIR/crons/"
cp -r /root/.openclaw/cron/jobs-state.json "$BACKUP_DIR/crons/" 2>/dev/null || true

# 导出 cron 可读列表
python3 -c "
import json
with open('/root/.openclaw/cron/jobs.json') as f:
    data = json.load(f)
lines = []
for j in data.get('jobs', []):
    en = '✅' if j.get('enabled') else '❌'
    name = j.get('name', j.get('id','')[:20])
    sch = j.get('schedule', {}).get('expr', 'N/A')
    lines.append(f'{en} {name}: {sch}')
with open('${BACKUP_DIR}/crons/cron_list.txt', 'w') as f:
    f.write('\n'.join(lines))
"

# === 7. 生成恢复脚本 ===
echo "📋 生成一键恢复脚本..."
cat > "${BACKUP_DIR}/restore.sh" << 'RESTORE_EOF'
#!/bin/bash
# ============================================================
# V4.0 一键恢复脚本
# 恢复范围: 配置文件、脚本、信号引擎、cron、模拟交易、日线缓存
# 不会覆盖: AGENTS.md / SOUL.md / USER.md / TOOLS.md / IDENTITY.md
# ============================================================
set -e

BACKUP_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKSPACE="/root/.openclaw/workspace"

echo "🔄 V4.0 系统恢复开始..."
echo "   源: $BACKUP_DIR"
echo "   目标: $WORKSPACE"
echo ""

# 确认
read -p "⚠️ 将覆盖当前系统文件，确认恢复？[y/N] " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "已取消"
    exit 0
fi

# 1. 脚本
echo "📦 恢复脚本..."
rsync -a "$BACKUP_DIR/scripts/" "$WORKSPACE/scripts/"

# 2. 信号引擎
echo "📦 恢复信号引擎..."
rsync -a "$BACKUP_DIR/stock-signals/" "$WORKSPACE/stock-signals/"

# 3. 模拟交易
echo "📦 恢复模拟交易..."
rsync -a "$BACKUP_DIR/sim_trading/" "$WORKSPACE/sim_trading/"

# 4. 报告模板
echo "📦 恢复报告模板..."
rsync -a "$BACKUP_DIR/docs/" "$WORKSPACE/docs/"

# 5. Cron
echo "📦 恢复 Cron 配置..."
cp "$BACKUP_DIR/crons/jobs.json" /root/.openclaw/cron/jobs.json

# 6. 核心文件（不覆盖个人配置）
echo "📦 恢复核心文件（跳过 AGENTS/SOUL/USER/TOOLS/IDENTITY）..."
for f in "$BACKUP_DIR"/*.sh "$BACKUP_DIR"/*.py "$BACKUP_DIR"/*.json "$BACKUP_DIR"/*.md; do
    [ -f "$f" ] || continue
    basename=$(basename "$f")
    case "$basename" in
        AGENTS.md|SOUL.md|USER.md|TOOLS.md|IDENTITY.md|HEARTBEAT.md)
            echo "  ⏭️ 跳过: $basename（个人配置）"
            ;;
        *)
            cp "$f" "$WORKSPACE/"
            echo "  ✅ $basename"
            ;;
    esac
done

# 7. 重启 gateway
echo ""
echo "🔁 重启 Gateway..."
openclaw gateway restart

echo ""
echo "✅ V4.0 恢复完成！"
echo "   个人配置文件（AGENTS/SOUL/USER/TOOLS/IDENTITY/HEARTBEAT）已保留未覆盖"
RESTORE_EOF
chmod +x "${BACKUP_DIR}/restore.sh"

# === 8. 生成版本说明 ===
cat > "${BACKUP_DIR}/VERSION.md" << 'VERSION_EOF'
# 股票量化系统 V4.0

**备份时间：** 2026-05-29 16:43 CST
**备份原因：** 完成 6 项修复后的系统快照

## V4.0 与 V3.0 的差异

### 新增
- `scripts/signals_summary.py` - 信号引擎摘要生成器（189KB→10KB，保留全量信号）
- `layer_monitor.py` 集成自动调用 signals_summary.py

### 修复
1. `monitor_full.py` 硬编码持仓 → 动态读取 TOOLS.md
2. 5 个模拟交易 cron 补充 delivery.channel
3. 模拟交易盘中推送 → 已禁用，仅保留收盘报告

### Cron 变更
- 5 个模拟交易盘中 cron（09:35/10:30/11:25/13:35/14:30）→ 已禁用
- 3 个盘中监控 cron prompt → 更新引用 signals_summary.json
- 模拟交易-收盘报告 → 保留（盘后合并输出）

## 恢复说明
```bash
cd backups/v4.0_20260529_1643
bash restore.sh
```
恢复不会覆盖 AGENTS.md / SOUL.md / USER.md / TOOLS.md / IDENTITY.md / HEARTBEAT.md。
VERSION_EOF

# === 9. 统计 ===
echo ""
echo "========================================="
echo "📊 V4.0 备份完成！"
echo "========================================="
echo "   位置: $BACKUP_DIR"
echo "   大小: $(du -sh "$BACKUP_DIR" | cut -f1)"
echo ""
echo "   包含:"
echo "   📄 核心配置: $(find "$BACKUP_DIR" -maxdepth 1 -name '*.md' -o -name '*.sh' -o -name '*.json' | wc -l) 个文件"
echo "   📜 脚本: $(find "$BACKUP_DIR/scripts" -type f | wc -l) 个"
echo "   📊 信号引擎: $(find "$BACKUP_DIR/stock-signals" -type f | wc -l) 个 (含 $(find "$BACKUP_DIR/stock-signals/cache" -name '*.day' | wc -l) 日线缓存)"
echo "   💰 模拟交易: $(find "$BACKUP_DIR/sim_trading" -type f | wc -l) 个"
echo "   📋 报告模板: $(find "$BACKUP_DIR/docs" -type f | wc -l) 个"
echo "   ⏰ Cron: $(cat "$BACKUP_DIR/crons/cron_list.txt" | wc -l) 个任务"
echo ""
echo "   一键恢复: bash $BACKUP_DIR/restore.sh"
echo ""
