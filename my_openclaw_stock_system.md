# OpenClaw 股票量化分析与交易系统 V4.0

> 架构文档 · 2026-05-29  
> 基于 OpenClaw Gateway 的完整量化交易系统，涵盖信号引擎、实时监控、模拟交易、全链路报告、双色球预测

---

## 一、系统总览

```
my_openclaw_stock_system/
├── .openclaw/                     # OpenClaw Gateway 配置（部署时由 OpenClaw 管理）
│   └── cron/jobs.json             # ← 18个定时任务配置（核心）
│
├── stock-signals/                 # 📊 信号引擎（29规则 V3.0）
│   ├── engine.sh                  #   主引擎：全池行情拉取+29规则评分
│   ├── rules/                     #   规则模块（14条规则，7维度）
│   │   ├── kline.sh               #     K线形态（红三兵/射击之星/锤子/十字星等）
│   │   ├── trend.sh               #     均线趋势（排列/交叉/发散/收敛）
│   │   ├── volume.sh              #     量价关系（量比/缩量/放量突破）
│   │   ├── support_resistance.sh  #     支撑阻力（突破/2B/筹码密集/前高）
│   │   ├── sector.sh              #     板块联动（相对强度/该涨不涨）
│   │   ├── entry_exit.sh          #     风控（入场止损/动态止盈）
│   │   ├── rsi.sh                 #     RSI超买超卖
│   │   ├── price.sh               #     价格异动
│   │   ├── market_filter.sh       #     大盘过滤器
│   │   └── historical_resistance.py #   历史阻力位计算
│   ├── cache/                     #   日线缓存（143只标的，每日自动更新）
│   ├── price_cache.sh             #   缓存管理（update/status/backfill）
│   ├── backfill_cache.sh          #   全量回补（首次部署/加新标的）
│   ├── signal_dedup.py            #   信号去重
│   ├── backtest_p0.py             #   P0评分回测
│   └── concept_map.json           #   概念板块映射
│
├── scripts/                       # 🔧 核心脚本
│   ├── tools.sh                   # ← 统一数据源入口（所有cron唯一数据获取点）
│   ├── layer_monitor.py           #   分层监控采集引擎（L1-L6 + 信号摘要）
│   ├── signals_summary.py         #   信号摘要生成（189KB→10KB）
│   ├── l6_hot_alerts.py           #   热点异动分析+动态升降级
│   ├── smart_monitor.sh           #   智能监控推送V5（大盘+板块+持仓+机会）
│   ├── run_engine.sh              #   引擎信号采集包装器（进程锁+收盘校准）
│   ├── monitor_full.py            #   全量持仓+算力板块监控
│   ├── top5.py                    #   Top5精选评分排名（独立评分，不依赖engine.sh）
│   ├── spot_monitor.py            #   盘中定点监控（止损/清仓线+指数趋势）
│   ├── 风口-个股映射.json          #   风口→个股映射配置
│   ├── ocr_image.sh               #   截图OCR（券商交割单识别）
│   └── backup_v4.sh               #   V4.0备份脚本
│
├── sim_trading/                   # 💰 模拟交易系统 V2.0
│   ├── sim_engine.py              #   交易引擎（信号驱动+T+1约束）
│   ├── run_sim.sh                 #   启动脚本
│   ├── account.json               #   账户状态（资金/持仓/历史）
│   ├── data/                      #   每日信号数据
│   ├── logs/                      #   交易日志
│   └── reports/                   #   每日交割报告
│
├── docs/                          # 📐 报告模板与机制文档
│   ├── 个股深度分析报告模板.md      #   个股分析v2.0模板
│   ├── 盘前作战指令模板.md          #   08:30操作指令
│   ├── 竞价验证模板.md             #   09:25竞价验证
│   ├── 午间修正模板.md             #   12:00午间修正
│   ├── 盘后交割单与绩效审计模板.md   #   15:30绩效审计
│   ├── 结构重塑报告模板.md          #   21:00市场结构
│   ├── 信息炼金报告模板.md          #   22:00风口研报
│   ├── 周末风口研报模板.md          #   周日21:00
│   ├── 全链路报告闭环体系.md        #   报告体系总纲
│   ├── 监控报告体系运行机制.md      #   监控系统运行机制
│   ├── 盘中监控工作机制.md          #   盘中监控工作流
│   ├── 盘前作战工作机制.md          #   盘前作战工作流
│   ├── 竞价验证工作机制.md          #   竞价验证工作流
│   ├── 午间修正工作机制.md          #   午间修正工作流
│   ├── 盘后审计工作机制.md          #   盘后审计工作流
│   ├── 结构重塑工作机制.md          #   结构重塑工作流
│   ├── 风口研报工作机制.md          #   信息炼金工作流
│   └── 模拟交易工作机制.md          #   模拟交易工作流
│
├── reports/                       # 📝 历史报告存档（按日期命名）
│   └── 2026-05-DD-HHMM-报告名.md
│
├── memory/                        # 🧠 每日工作日志
├── TOOLS.md                       # ⚙️ 持仓/自选/监控池（唯一真实持仓数据源）
├── AGENTS.md                      # 工作准则与规则
├── SOUL.md                        # 创作人格
├── USER.md                        # 用户画像
├── IDENTITY.md                    # AI身份
├── HEARTBEAT.md                   # 心跳检查
├── DAILY_REPORT_FRAMEWORK.md      # 报告框架v2.0
├── LOTTERY.md                     # 双色球预测工作流
├── ssq_history_2025_2026.json     # 双色球历史数据
├── PORTFOLIO.md                   # 投资组合管理文档
├── ROLES.md                       # 角色定义
├── info-sources.md                # 信息源配置
└── engine.sh                      #  顶层入口脚本（指向stock-signals/engine.sh）
```

---

## 二、数据流架构

```
                         TOOLS.md (持仓+自选+监控池)
                              │
                              ▼
                    tools.sh (统一数据源入口)
                    ├── holdings  → 当前持仓（股数+成本）
                    ├── history   → 历史自选池
                    └── monitor   → 全量监控扫描
                         │
                         ├──────────────────────────┐
                         ▼                          ▼
              stock-signals/engine.sh        scripts/layer_monitor.py
              (29规则信号引擎V3.0)           (分层监控采集引擎)
              │                              │
              │ 一次curl全池行情              │ 批量curl → 分层计算
              │ 本地日线缓存计算MA/MACD       │ L1: 大盘  L2: 持仓
              │ 逐条规则评分 → 共振判定       │ L3: 重点  L4: ETF/概念
              │ 信号去重 → 去噪               │ L5: 自选异动  L6: 热点
              │                              │ urgent: 紧急信号
              ▼                              ▼
    /tmp/stock_alerts/             /tmp/stock_alerts/
    engine_signals.json            L1-L6.txt + urgent.txt
    (189KB,120只标的全量信号)      (12.7KB,分层摘要)
              │                              │
              └──────────┬───────────────────┘
                         ▼
              scripts/signals_summary.py
              (自动生成 10KB 结构化摘要)
              signals_summary.json
                         │
                         ▼
              18个Cron任务读取数据源
              ├── 08:30 盘前作战指令
              ├── 09:25 早盘竞价验证
              ├── 09:35-11:55 盘中监控(早盘×2)
              ├── 12:00 午间盘面复盘
              ├── 13:05-14:55 盘中监控(午盘)
              ├── 15:05 收盘日线追加
              ├── 15:10 模拟交易收盘
              ├── 15:30 盘后交割单
              ├── 16:00 收盘数据快照
              ├── 16:15 行情缓存更新
              ├── 21:00 结构重塑
              └── 22:00 信息炼金
```

---

## 三、核心组件详解

### 3.1 信号引擎 V3.0（`stock-signals/engine.sh`）

**功能：** 一次curl拉取全池（120+标的）实时行情，本地日线缓存计算MA/MACD/RSI等衍生指标，逐条规则评分，输出共振判定。

**29条规则，7个维度：**

| 维度 | 规则数 | 输出 |
|:--|:--:|:--|
| K线形态 | 6 | 红三兵、射击之星、锤子线、上吊线、十字星、缺口 |
| 均线趋势 | 7 | 多头/空头排列、金叉/死叉、偏离度、MACD底/顶背离 |
| 量价关系 | 4 | 量比、价涨量增/价跌量缩、换手率分级、缩量后放量突破 |
| 支撑阻力 | 6 | 突破前高、2B假突破、前高压制、跌破支撑、筹码密集、均线收敛 |
| 板块联动 | 3 | 板块相对强度、该涨不涨、该跌不跌 |
| 风控管理 | 2 | 入场止损位、动态止盈 |
| RSI+价格 | 1+1 | RSI超买(>70)/超卖(<30)、价格异动(涨跌幅分级) |

**输出：** 每只标的→`resonance` 共振判定（三重共振-出手/双重确认-可参与/单一信号-观察/卖出确认-减仓）+ `total_score_ext` 含形态评分

**依赖：**
- `curl` 腾讯行情API (gtimg.cn)
- 日线缓存 (`cache/*.day`, baostock格式)
- `stock-signals/rules/*.sh` 规则模块
- `scripts/tools.sh holdings` 确定全池标的范围

### 3.2 分层监控采集引擎（`scripts/layer_monitor.py`）

**功能：** 批量curl全量行情(150+只)，计算分层指标，写入 `/tmp/stock_alerts/` 供cron读取。**从不超时**，只做采集不做推送。

**输出文件(L1-L6)：**

| 文件 | 大小 | 内容 |
|:--|:--:|:--|
| L1_market.txt | ~250B | 四指数+趋势判断 |
| L2_holdings.txt | ~550B | 持仓（含止损/止盈逼近标记） |
| L3_focus.txt | ~2.4KB | 重点监控池（含介入价/触发状态） |
| L4_etf_concept.txt | ~300B | ETF盈亏/概念板块对比 |
| L5_watchlist.txt | ~3KB | 全量自选异动（±2%过滤） |
| L6_hot_alerts.txt | ~5.5KB | 热点异动深度分析+升降级 |
| urgent.txt | ~700B | 紧急信号（跌停/止损触发） |
| engine_signals.json | ~189KB | 全量引擎信号（原始数据，保留盘后用） |
| signals_summary.json | ~10KB | **信号引擎结构摘要（盘中cron读取此文件）** |

### 3.3 信号摘要生成器（`scripts/signals_summary.py`）

**功能：** 将189KB的 engine_signals.json 压缩为10KB的结构化摘要，保留所有核心维度：共振判定、形态信号(24种)、评分TOP15、紧急L3信号、止损告警。

**设计原则：零信息丢失。** 每条K线形态信号从原始engine_signals中提取，按标的去重，按优先级排序。LLM可以直接读这个文件替代原始189KB。

### 3.4 统一数据源（`scripts/tools.sh`）

**功能：** 所有cron任务获取持仓/自选/监控数据的唯一入口。

```
bash tools.sh holdings   → 当前持仓（代码 名称 股数 成本）
bash tools.sh history    → 历史自选池（代码 名称）
bash tools.sh monitor    → 全量监控扫描
bash tools.sh signals    → 信号汇总
```

**持仓获取方式：** 实时从 `TOOLS.md` grep `^### 持仓` 段提取，不缓存、不硬编码。

### 3.5 Top5精选评分（`scripts/top5.py`）

**功能：** 独立于engine.sh的评分系统，直接从gtimg行情+日线缓存计算。5因子模型：涨幅因子+量能因子+位置因子+趋势因子+形态因子。

**输出：** 全池评分TOP5，P0≥2.5达标介入线。

### 3.6 模拟交易引擎（`sim_trading/sim_engine.py`）

**功能：** 信号驱动的模拟交易，集成engine.sh信号。T+1约束，50万初始资金。每日09:35/10:30/11:25/13:35/14:30扫描信号，15:10出收盘报告。

**当前状态：** 盘中5个cron已禁用（2026-05-29），仅保留15:10收盘报告，盘后和交割单一起合并输出。

### 3.7 盘中监控cron（3个）

| Cron | 调度 | 读取数据 |
|:--|:--|:--|
| 盘中监控-早盘 | 09:35, 09:50 | L1-L6.txt + signals_summary.json |
| 盘中监控-早盘B | 10:05/20/35/50, 11:05/20/35/50 | 同上 |
| 盘中监控-午盘 | 13:05/20/35/50, 14:05/20/35/50 | 同上 |

**prompt要求LLM输出6模块：** ①大盘一句话 ②持仓（止损止盈逼近标红，标注形态信号规则） ③重点监控（区分持仓/非持仓，标注共振判定） ④ETF/概念异动 ⑤自选异动+热点深度分析 ⑥紧急信号

### 3.8 报告cron（7个）

| 时间 | 报告 | 模板 | 间呼应 |
|:--|:--|:--|:---|
| 08:30 | 盘前作战指令 | docs/盘前作战指令模板.md | 引述昨晚信息炼金+结构重塑核心结论 |
| 09:25 | 早盘竞价验证 | docs/竞价验证模板.md | 引用08:30盘前作战，看竞价验证/挑战预期 |
| 12:00 | 午间盘面复盘 | docs/午间修正模板.md | 引用09:25竞价验证+早盘监控数据 |
| 15:30 | 盘后交割单 | docs/盘后交割单与绩效审计模板.md | 引述08:30关键判断→呼应12:00分析→预告21:00 |
| 21:00 | 市场结构重塑 | docs/结构重塑报告模板.md | 引述15:30绩效审计→预判22:00信息炼金 |
| 22:00 | 信息炼金 | docs/信息炼金报告模板.md | 引述21:00结构重塑→呼应今日交割→预告明日盘前 |
| 周日21:00 | 周末风口研报 | docs/周末风口研报模板.md | 独立报告，为周一08:30提供输入 |

---

## 四、数据源依赖

| 数据 | 来源 | 接口 | 频率 |
|:--|:--|:--|:--|
| 实时行情 | 腾讯 gtimg.cn | curl | 每次采集 |
| 日线K线(历史) | baostock | Python SDK | 每日收盘后 |
| 日线缓存(本地) | 本地文件 | cache/*.day | 每次评分 |
| 持仓数据 | TOOLS.md | grep | 每次cron执行 |
| 新闻/研报/风口 | LLM web_search + 同花顺/东方财富 | Agent工具 | 报告生成时 |
| 双色球开奖 | 500.com | curl | 开奖日12:00/22:30 |

---

## 五、部署流程

### 5.1 新环境部署步骤

```bash
# 1. 安装 OpenClaw Gateway（按官方文档）
curl -fsSL https://docs.openclaw.ai/install.sh | bash

# 2. 克隆本项目
git clone <repo> /root/.openclaw/workspace

# 3. 初始化日线缓存（首次部署必需）
bash stock-signals/backfill_cache.sh

# 4. 配置 TOOLS.md（填入你的持仓/自选）

# 5. 导入 cron 任务
cp crons/jobs.json /root/.openclaw/cron/jobs.json

# 6. 安装 Python 依赖
pip install baostock

# 7. 安装系统依赖
apt-get install -y jq curl bc tesseract-ocr tesseract-ocr-chi-sim

# 8. 启动 OpenClaw Gateway
openclaw gateway restart
```

### 5.2 一键恢复

```bash
cd backups/v4.0_20260529_1643
bash restore.sh
```

恢复不会覆盖个人配置（AGENTS.md / SOUL.md / USER.md / TOOLS.md / IDENTITY.md / HEARTBEAT.md）。

---

## 六、过期待清理文件清单

以下文件是系统演进中遗留的早期版本，**已被新组件替代，可安全删除**：

| 文件 | 原因 |
|:--|:--|
| scripts/monitor_603667.sh | 五洲新春专属监控，已清仓 |
| scripts/monitor_entry_0525.sh | 0525一次性介入信号脚本 |
| scripts/monitor_suanli.py | 已被 layer_monitor.py 覆盖 |
| scripts/monitor_suanli.sh | 同上 |
| scripts/monitor_all.sh | 已被 smart_monitor.sh 替代 |
| scripts/realtime_monitor.py | 已被 layer_monitor.py 替代 |
| scripts/realtime_trader.py | 已被 sim_engine.py 替代 |
| scripts/check_signals.sh | 功能已集成到 tools.sh |
| scripts/verify_first_tier.sh | 功能已集成到 top5.py |
| scripts/top5.sh | 已被 top5.py 替代 |
| scripts/top5_ratings.sh | 已被 top5.py 替代 |
| scripts/weekend_report.sh | 周末cron已直接用prompt处理 |
| stock-signals/price_cache.sh.bak | 备份文件（当前版本已不含此bug） |
| stock-signals/test_300620.sh | 一次性测试脚本 |
| stock-signals/cache/*.day.tmp | 临时文件 |
| stocks/ | 整个目录可删除（仅含五洲新春已清仓数据） |
| 监控/ | 早期手工配置，已被layer_monitor+18个cron替代 |
| trade_log/ | 早期交易日志，已被sim_trading替代 |
| reports/parse_ssq.py | 应移入 scripts/ |
| reports/午间报告_20260514.md | 早期未按命名规则的报告 |
| reports/盘后交割单_20260514.md | 同上 |
| reports/信息炼金-2026-05-13.md | 同上 |
| reports/P0_回测报告_20260523.md | 回测报告，可移至 stock-signals/ |
| projects/ | 不属于股票系统（superbaby项目文件） |
| agent-1f69acd9/ | 子agent目录，非系统组件 |

---

## 七、关键设计决策

1. **统一数据源** → `tools.sh` 是所有cron获取持仓的唯一入口，杜绝硬编码
2. **分层采集 + 摘要压缩** → layer_monitor采集 → signals_summary压缩 → cron读取10KB而非189KB
3. **进程锁** → run_engine.sh 使用 `flock` 防止多cron并发执行引擎
4. **收盘校准** → 15:05后用gtimg实时API覆盖盘中最后一轮快照的price字段
5. **T+1铁律** → 模拟交易sim_engine强制当日买入次日可卖
6. **停牌保护** → layer_monitor检测无gtimg数据的标的，标记为已停牌不参与评分
7. **个人配置隔离** → AGENTS/SOUL/USER/TOOLS/IDENTITY/HEARTBEAT 不进版本控制，备份恢复时自动跳过

---

*系统版本：V4.0 | 审计日期：2026-05-29 | 基于 OpenClaw Gateway*
