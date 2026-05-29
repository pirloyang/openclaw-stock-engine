# OpenClaw Stock Engine V4.0

基于 [OpenClaw](https://github.com/nicedoc/openclaw) 构建的 A 股量化分析与交易系统。

## 核心能力

- **信号引擎 V3.0**：29 条规则 × 7 维度，一次 curl 拉取全池 120+ 标的实时行情
- **分层监控体系**：L1-L6 六层数据采集（大盘→持仓→重点→ETF→自选→热点）
- **全链路报告闭环**：08:30 盘前作战 → 09:25 竞价验证 → 盘中监控 → 12:00 午间修正 → 15:30 盘后交割 → 21:00 结构重塑 → 22:00 信息炼金
- **模拟交易 V2.0**：信号驱动 + T+1 约束
- **信号摘要压缩**：189KB engine_signals → 10KB 结构化摘要

## 快速开始

```bash
# 1. 安装 OpenClaw Gateway
curl -fsSL https://docs.openclaw.ai/install.sh | bash

# 2. 克隆仓库
git clone https://github.com/pirloyang/openclaw-stock-engine.git /root/.openclaw/workspace

# 3. 初始化日线缓存（首次部署必需）
bash stock-signals/backfill_cache.sh

# 4. 配置持仓
#   编辑 TOOLS.md 填入你的持仓和自选（参考模板）

# 5. 导入 cron 定时任务
cp crons/jobs.json /root/.openclaw/cron/jobs.json

# 6. 安装依赖
pip install baostock

# 7. 启动
openclaw gateway restart
```

## 系统架构

```
TOOLS.md (持仓/自选/监控池)
    │
    ▼
tools.sh (统一数据源入口)
    │
    ├─→ stock-signals/engine.sh ──→ engine_signals.json (189KB)
    │                              └→ signals_summary.py ──→ signals_summary.json (10KB)
    │
    ├─→ scripts/layer_monitor.py ──→ L1-L6.txt (12KB 分层摘要)
    │
    └─→ 18个 Cron 任务读取数据源，生成全链路报告
```

## 目录结构

```
stock-signals/    # 信号引擎（29 规则 + 日线缓存）
scripts/          # 核心脚本（采集/监控/评分/工具）
sim_trading/      # 模拟交易系统
docs/             # 报告模板与机制文档
crons/            # Cron 配置（18 个定时任务）
examples/         # 个人配置示例
```

## 注意事项

- 首次部署务必运行 `backfill_cache.sh` 初始化日线缓存
- 持仓数据需编辑 TOOLS.md 填入真实持仓
- 个人配置（AGENTS/SOUL/USER/TOOLS/IDENTITY）不在版本控制中
- 历史报告和模拟交易数据不纳入 Git

## License

MIT
