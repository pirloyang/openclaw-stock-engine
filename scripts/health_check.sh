#!/usr/bin/env bash
set -euo pipefail

# ──────────────────────────────────────────────
# health_check.sh — 股票量化系统端到端健康检查
# 设计原则：端到端链路追踪 > 单点文件存在性
# 用法：bash scripts/health_check.sh [--json]
# 输出：控制台摘要 + data/health_check_latest.json
# ──────────────────────────────────────────────

WS="${WORKSPACE_ROOT:-/root/.openclaw/workspace}"
SIGNAL_DIR="/tmp/stock_alerts"
TODAY=$(date +%Y-%m-%d)
PASS=0; FAIL=0; WARN=0
RESULTS=()
OUTPUT_JSON=false

[[ "${1:-}" == "--json" ]] && OUTPUT_JSON=true

# ── 工具函数 ──
log_pass() { PASS=$((PASS+1)); RESULTS+=("{\"id\":\"$1\",\"status\":\"PASS\",\"msg\":\"$2\"}"); echo "  ✅ [$1] $2"; }
log_fail() { FAIL=$((FAIL+1)); RESULTS+=("{\"id\":\"$1\",\"status\":\"FAIL\",\"msg\":\"$2\"}"); echo "  ❌ [$1] $2"; }
log_warn() { WARN=$((WARN+1)); RESULTS+=("{\"id\":\"$1\",\"status\":\"WARN\",\"msg\":\"$2\"}"); echo "  ⚠️  [$1] $2"; }

section() { echo ""; echo "═══ $1 ═══"; }

# ──────────────────────────────────────────────
# L1: 数据流水线端到端
# pipeline.sh → engine.sh → layer_monitor → L1~L6/urgent.txt
# ──────────────────────────────────────────────
section "L1 数据流水线端到端"

# 检查信号引擎产物完整性（6层+urgent）
LAYERS=("L1_market" "L2_holdings" "L3_focus" "L4_etf_concept" "L5_watchlist" "L6_hot_alerts" "urgent")
ALL_LAYERS_EXIST=true
for layer in "${LAYERS[@]}"; do
    if [[ -f "$SIGNAL_DIR/${layer}.txt" ]]; then
        size=$(wc -c < "$SIGNAL_DIR/${layer}.txt")
        if [[ $size -lt 10 ]]; then
            ALL_LAYERS_EXIST=false
            log_warn "PIPE-${layer}" "文件存在但过小 (${size}B)，可能为空信号"
        fi
    else
        ALL_LAYERS_EXIST=false
        log_fail "PIPE-${layer}" "$SIGNAL_DIR/${layer}.txt 不存在"
    fi
done
if $ALL_LAYERS_EXIST; then
    log_pass "PIPE-ALL-LAYERS" "L1~L6 + urgent 全部产出完整"
fi

# JSON 产物
for jf in all_signals.json signals_summary.json sector_flow.json; do
    if [[ -f "$SIGNAL_DIR/$jf" ]]; then
        # 验证是合法 JSON
        if python3 -c "import json; json.load(open('$SIGNAL_DIR/$jf'))" 2>/dev/null; then
            log_pass "PIPE-JSON-$jf" "合法 JSON"
        else
            log_fail "PIPE-JSON-$jf" "JSON 解析失败"
        fi
    else
        log_warn "PIPE-JSON-$jf" "不存在（可能盘后清空）"
    fi
done

# pipeline.sh 本身可执行
if [[ -x "$WS/scripts/pipeline.sh" ]]; then
    log_pass "PIPE-SCRIPT" "pipeline.sh 可执行"
else
    log_fail "PIPE-SCRIPT" "pipeline.sh 不可执行或不存在"
fi

# ──────────────────────────────────────────────
# L2: 净值快照端到端
# portfolio_engine.py → portfolio_snapshot.json
# ──────────────────────────────────────────────
section "L2 净值快照端到端"

SNAPSHOT="$WS/data/portfolio_snapshot.json"
if [[ -f "$SNAPSHOT" ]]; then
    # 验证 JSON + 关键字段
    snap_ok=$(python3 -c "
import json
d = json.load(open('$SNAPSHOT'))
fields = ['date','net_worth','market_value','positions']
missing = [f for f in fields if f not in d]
if missing:
    print(f'MISSING_FIELDS:{missing}')
else:
    # 检查日期不超3天（超3天=引擎没跑）
    from datetime import datetime, timedelta
    snap_date = d['date']
    days_old = (datetime.now() - datetime.strptime(snap_date, '%Y-%m-%d')).days
    if days_old > 3:
        print(f'STALE:{days_old}d')
    else:
        print('OK')
" 2>&1)
    case "$snap_ok" in
        OK*)          log_pass "SNAPSHOT-VALID" "portfolio_snapshot.json 有效且 ≤3天" ;;
        STALE:*)      log_fail "SNAPSHOT-STALE" "快照 ${snap_ok#STALE:} 未更新，引擎可能没跑" ;;
        MISSING*)     log_fail "SNAPSHOT-FIELDS" "缺字段: ${snap_ok#MISSING_FIELDS:}" ;;
        *)            log_fail "SNAPSHOT-PARSE" "解析失败: $snap_ok" ;;
    esac
else
    log_fail "SNAPSHOT-EXIST" "portfolio_snapshot.json 不存在"
fi

# ──────────────────────────────────────────────
# L3: 盘后结算审计链
# portfolio_snapshot.json → daily_settlement.py → settlement.json
# ──────────────────────────────────────────────
section "L3 盘后结算审计链"

SETTLE="$WS/data/settlement.json"
if [[ -f "$SETTLE" ]]; then
    settle_ok=$(python3 -c "
import json
d = json.load(open('$SETTLE'))
if 'date' not in d:
    print('NO_DATE')
elif 'holdings' not in d and 'positions' not in d:
    print('NO_HOLDINGS')
else:
    from datetime import datetime
    days_old = (datetime.now() - datetime.strptime(d['date'], '%Y-%m-%d')).days
    print(f'OK:{days_old}d' if days_old <= 3 else f'STALE:{days_old}d')
" 2>&1)
    case "$settle_ok" in
        OK:*)         log_pass "SETTLE-CHAIN" "settlement.json 有效" ;;
        STALE:*)      log_warn "SETTLE-STALE" "结算数据 ${settle_ok#STALE:} 未更新" ;;
        NO_DATE)      log_fail "SETTLE-NO-DATE" "settlement.json 缺 date 字段" ;;
        NO_HOLDINGS)  log_fail "SETTLE-NO-POS" "settlement.json 缺 holdings/positions 字段" ;;
        *)            log_fail "SETTLE-PARSE" "解析失败: $settle_ok" ;;
    esac
else
    log_fail "SETTLE-EXIST" "settlement.json 不存在，盘后审计链断裂"
fi

# ──────────────────────────────────────────────
# L4: 持仓→计价解析正确
# TOOLS.md持仓节 → _parse_real_positions → compute_net_worth
# ──────────────────────────────────────────────
section "L4 持仓解析端到端"

TOOLS="$WS/TOOLS.md"
if [[ -f "$TOOLS" ]]; then
    # 检查持仓节有有效持仓行（成本¥）
    hold_count=$(cd "$WS" && python3 -c "
import sys; sys.path.insert(0,'scripts')
from portfolio_engine import _parse_real_positions
print(len(_parse_real_positions()))
" 2>/dev/null || echo "0")
    hold_count=$(echo "$hold_count" | tr -d '\n')
    if [[ "$hold_count" -ge 1 ]]; then
        log_pass "HOLDINGS-PARSE" "TOOLS.md 解析到 ${hold_count} 只持仓"
    else
        log_warn "HOLDINGS-EMPTY" "TOOLS.md 无有效持仓行（可能空仓）"
    fi
else
    log_fail "TOOLS-EXIST" "TOOLS.md 不存在"
fi

# ──────────────────────────────────────────────
# L5: 重点关注混入分层流水线
# focus_watchlist.json → layer_monitor → L3_focus.txt
# ──────────────────────────────────────────────
section "L5 重点关注→L3_focus"

FOCUS="$WS/stock-signals/focus_watchlist.json"
L3="$SIGNAL_DIR/L3_focus.txt"

if [[ -f "$FOCUS" ]]; then
    focus_count=$(python3 -c "import json; d=json.load(open('$FOCUS')); print(len(d.get('focus_list',d.get('stocks',[]))))" 2>/dev/null || echo "0")
    if [[ $focus_count -ge 1 ]]; then
        log_pass "FOCUS-JSON" "focus_watchlist.json 含 ${focus_count} 只标的"
    else
        log_warn "FOCUS-EMPTY" "focus_watchlist.json 无标的"
    fi
else
    log_fail "FOCUS-EXIST" "focus_watchlist.json 不存在"
fi

if [[ -f "$L3" ]]; then
    l3_size=$(wc -c < "$L3")
    if [[ $l3_size -gt 50 ]]; then
        log_pass "L3-OUTPUT" "L3_focus.txt 有内容 (${l3_size}B)"
    else
        log_warn "L3-OUTPUT" "L3_focus.txt 过小 (${l3_size}B)"
    fi
else
    log_warn "L3-OUTPUT" "L3_focus.txt 不存在（盘后可能清空）"
fi

# ──────────────────────────────────────────────
# L6: Cron 任务活性
# ──────────────────────────────────────────────
section "L6 Cron 任务活性"

# 用网关 cron jobs.json 检查
CRON_FILE="$HOME/.openclaw/cron/jobs.json"
if [[ -f "$CRON_FILE" ]]; then
    cron_info=$(python3 -c "
import json
jobs = json.load(open('$CRON_FILE'))['jobs']
enabled = [j for j in jobs if j.get('enabled')]
errors = [j for j in enabled if j.get('lastError')]
stale = []
from datetime import datetime, timedelta
for j in enabled:
    lr = j.get('lastRunAtMs')
    if lr:
        last = datetime.fromtimestamp(lr/1000)
        if (datetime.now() - last).days > 7:
            stale.append(j.get('name','?'))
print(f'total={len(jobs)} enabled={len(enabled)} errors={len(errors)} stale={len(stale)}')
if stale: print('STALE:' + ','.join(stale))
if errors:
    for e in errors:
        print(f'ERR:{e.get(\"name\",\"?\")}:{str(e.get(\"lastError\",\"\"))[:80]}')
" 2>&1)
    echo "  $cron_info" | head -5

    total=$(echo "$cron_info" | grep -oP 'total=\K[0-9]+' || echo "?")
    enabled=$(echo "$cron_info" | grep -oP 'enabled=\K[0-9]+' || echo "?")
    errors=$(echo "$cron_info" | grep -oP 'errors=\K[0-9]+' || echo "0")

    if [[ "$errors" == "0" ]]; then
        log_pass "CRON-NO-ERRORS" "${enabled}/${total} 启用，0 错误"
    else
        log_fail "CRON-HAS-ERRORS" "${errors} 个 cron 最近报错"
        echo "$cron_info" | grep '^ERR:' | while read line; do
            echo "    ↳ $line"
        done
    fi

    if echo "$cron_info" | grep -q '^STALE:'; then
        stale_names=$(echo "$cron_info" | grep '^STALE:' | cut -d: -f2-)
        log_warn "CRON-STALE" "超 7 天未运行: $stale_names"
    fi
else
    log_fail "CRON-FILE" "jobs.json 不存在"
fi

# ──────────────────────────────────────────────
# L7: 规则覆盖完整性
# ──────────────────────────────────────────────
section "L7 规则覆盖完整性"

RULE_DIR="$WS/docs"
REQUIRED_RULES=(
    "交易风控规则.md"
    "持仓变更登记规则.md"
    "重点关注清单维护规则.md"
    "信号引擎规则手册.md"
    "数据流水线规则.md"
    "信息炼金工作机制.md"
    "模拟交易工作机制.md"
    "每周复盘工作机制.md"
    "月度绩效审计规则.md"
    "里程碑告警规则.md"
    "信号回测验证规则.md"
    "cron治理规则.md"
    "持仓变更登记规则.md"
)
# 去重
UNIQUE_RULES=($(echo "${REQUIRED_RULES[@]}" | tr ' ' '\n' | sort -u))

missing_rules=0
for rule in "${UNIQUE_RULES[@]}"; do
    if [[ -f "$RULE_DIR/$rule" ]]; then
        :
    else
        missing_rules=$((missing_rules+1))
        log_fail "RULE-MISSING" "$rule"
    fi
done
if [[ $missing_rules -eq 0 ]]; then
    log_pass "RULES-ALL" "${#UNIQUE_RULES[@]} 份规则文件完整"
fi

# ──────────────────────────────────────────────
# L8: 部署一致性
# ──────────────────────────────────────────────
section "L8 部署一致性"

MANIFEST="$WS/deploy/cron_manifest.json"
if [[ -f "$MANIFEST" ]]; then
    manifest_count=$(python3 -c "import json; print(len(json.load(open('$MANIFEST')).get('templates',json.load(open('$MANIFEST')).get('jobs',[]))))" 2>/dev/null || echo "0")
    if [[ "$manifest_count" -eq 25 ]]; then
        log_pass "DEPLOY-MANIFEST" "cron_manifest 含 25 个模板"
    else
        log_warn "DEPLOY-MANIFEST" "期望 25，实际 ${manifest_count}"
    fi
else
    log_warn "DEPLOY-MANIFEST" "cron_manifest.json 不存在"
fi

# 检查 .env 是否有真实值（不含占位符）
if [[ -f "$WS/deploy/.env" ]]; then
    empty_vars=$(grep -cE '^[A-Z_]+=$' "$WS/deploy/.env" 2>/dev/null | tr -d '\n' || echo "0")
    if [[ "$empty_vars" -eq 0 ]]; then
        log_pass "DEPLOY-ENV" "deploy/.env 无空变量"
    else
        log_warn "DEPLOY-ENV" "${empty_vars} 个环境变量未填值"
    fi
else
    log_warn "DEPLOY-ENV" "deploy/.env 不存在（首次部署正常）"
fi

# gateway config template 存在
if [[ -f "$WS/deploy/gateway_config.template.json" ]]; then
    log_pass "DEPLOY-GW-TEMPLATE" "gateway_config.template.json 存在"
else
    log_warn "DEPLOY-GW-TEMPLATE" "gateway config 模板未导出"
fi

# ──────────────────────────────────────────────
# L9: 外部依赖连通性
# ──────────────────────────────────────────────
section "L9 外部依赖连通性"

# akshare
ak_ok=$(python3 -c "import akshare; print('OK')" 2>&1 || echo "FAIL")
if [[ "$ak_ok" == "OK" ]]; then
    log_pass "DEP-AKSHARE" "akshare 可导入"
else
    log_fail "DEP-AKSHARE" "akshare 导入失败: $ak_ok"
fi

# tesseract
if command -v tesseract >/dev/null 2>&1; then
    chi_sim=$(tesseract --list-langs 2>&1 | grep -c chi_sim || echo "0")
    if [[ "$chi_sim" -ge 1 ]]; then
        log_pass "DEP-TESSERACT" "tesseract + chi_sim 可用"
    else
        log_fail "DEP-TESSERACT" "tesseract 缺 chi_sim 语言包"
    fi
else
    log_fail "DEP-TESSERACT" "tesseract 未安装"
fi

# jq
if command -v jq >/dev/null 2>&1; then
    log_pass "DEP-JQ" "jq 可用"
else
    log_fail "DEP-JQ" "jq 未安装"
fi

# git
if command -v git >/dev/null 2>&1; then
    log_pass "DEP-GIT" "git 可用"
else
    log_fail "DEP-GIT" "git 未安装"
fi

# OpenClaw CLI
if command -v openclaw >/dev/null 2>&1; then
    log_pass "DEP-OPENCLAW" "openclaw CLI 可用"
else
    log_warn "DEP-OPENCLAW" "openclaw 不在 PATH（可能用 pnpm 运行）"
fi

# ──────────────────────────────────────────────
# 汇总
# ──────────────────────────────────────────────
TOTAL=$((PASS + FAIL + WARN))
SCORE=$(( PASS * 100 / (TOTAL > 0 ? TOTAL : 1) ))

echo ""
echo "══════════════════════════════════════"
echo "  体检结果：✅${PASS}  ❌${FAIL}  ⚠️${WARN}  评分 ${SCORE}/100"
echo "══════════════════════════════════════"

if [[ $FAIL -gt 0 ]]; then
    echo "  🔴 有 ${FAIL} 项致命问题需立即修复"
elif [[ $WARN -gt 0 ]]; then
    echo "  🟡 有 ${WARN} 项警告，建议关注"
else
    echo "  🟢 全部通过"
fi

# ── 写 JSON 报告 ──
REPORT="$WS/data/health_check_latest.json"
python3 -c "
import json, sys
from datetime import datetime, timezone, timedelta
results = [$(IFS=,; echo "${RESULTS[*]}")]
report = {
    'timestamp': datetime.now(timezone(timedelta(hours=8))).isoformat(),
    'date': '$TODAY',
    'summary': {'pass': $PASS, 'fail': $FAIL, 'warn': $WARN, 'score': $SCORE},
    'checks': [json.loads(r) for r in results]
}
json.dump(report, open('$REPORT', 'w'), ensure_ascii=False, indent=2)
print(f'📝 报告已写入 $REPORT')
"

# 非交易时段（收盘后/周末）才跑链路追踪的提示
HOUR=$(date +%H)
if [[ $HOUR -ge 9 && $HOUR -lt 15 ]]; then
    echo "  ⏰ 当前交易时段，部分链路产物可能是盘中状态"
fi

exit $FAIL
