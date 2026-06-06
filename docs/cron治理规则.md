# Cron 治理规则 v1.0

**生效日期：** 2026-06-06
**适用范围：** OpenClaw `cron` 工具创建/修改/启用/禁用所有 cron 任务
**配套依据：** `memory/*.md` 中十二轮事件总结、当前 26 个 cron 实战经验

> **核心定位：** Cron 是整个量化系统的"心跳"。任何 cron 配置错误都会直接影响推送、决策、数据生产。本规则定义"如何安全地创建、修改、运维 cron"。

---

## 一、投递通道治理（最重要）

### T1 · channel + accountId 必须配对
任何 `delivery.mode = "announce"` 的 cron 必须**同时**包含：

```json
{
  "delivery": {
    "mode": "announce",
    "channel": "openclaw-weixin",
    "to": "o9cq80z60LaB2jyf8JO9xNWsynN4@im.wechat",
    "accountId": "75c2b3e86437-im-bot"
  }
}
```

- 缺 `channel` → 报错 "requires channel"
- 缺 `to` → 报错 "requires target"
- 缺 `accountId` → 可能用错 bot 账号
- **三者一致性必须由本人发起的 cron 配置中明示**，禁止依赖默认值

### T2 · failureAlert 同样配对
关键 cron 必须配置 `failureAlert`，且字段配对规则同 T1：

```json
{
  "failureAlert": {
    "enabled": true,
    "after": 1,
    "cooldownMs": 3600000,
    "mode": "announce",
    "channel": "openclaw-weixin",
    "to": "o9cq80z60LaB2jyf8JO9xNWsynN4@im.wechat",
    "accountId": "75c2b3e86437-im-bot"
  }
}
```

**关键 cron 清单（必配 failureAlert）：**
- 盘前作战、竞价验证、午间修正、盘后审计、结构重塑、信息炼金
- 双色球预测/回顾
- 收盘缓存更新、盘后数据流水线

### T3 · channel 迁移禁令
当前 schema 仅允许 `openclaw-weixin`。任何尝试改成 `lightclawbot` 等其他渠道的修改都会被 schema 否决。
**05-31 ~ 06-02 已踩过此坑，禁止重复尝试。**

---

## 二、调度治理

### S1 · 时间表达式规范
- 使用 5 字段 cron（分 时 日 月 周）
- 必须显式声明 `tz: "Asia/Shanghai"`，禁止依赖默认时区
- 工作日：`* * 1-5`；包含周末：`* * 0-6`

### S2 · 调度密度上限
- 单一 cron 最小间隔 5 min
- 同时段（同一分钟）启动的 cron 不得超过 2 个
- pipeline 类（数据生产）使用 `staggerMs` 错开 2-5s

### S3 · timeout 设定
| Cron 类型 | timeoutSeconds 建议 |
|---|---|
| 数据生产（纯脚本执行） | 300-360 |
| 简短报告（盘中监控、竞价验证） | 600 |
| 复杂报告（盘前作战、信息炼金） | 600-900 |
| 周/月复盘 | 600-900 |
| 双色球预测 | 600 |

**禁止设置 `timeoutSeconds: 0`（无超时）**，会导致超时事件无法告警。

### S4 · 禁用随机延迟
- 禁止使用 `staggerMs` 超过 60s 用于"随机延迟"目的
- 单一 cron 启动时间应可预测，便于排查

---

## 三、Payload 治理

### P1 · sessionTarget 选择
| 场景 | sessionTarget | payload.kind |
|---|---|---|
| 报告/分析（默认）| `isolated` | `agentTurn` |
| 主会话事件注入 | `main` | `systemEvent` |
| 长期会话保留 | `session:<id>` | `agentTurn` |

**禁止：** sessionTarget=`current` 与 `agentTurn`，会污染主会话上下文

### P2 · lightContext 默认开启
所有 `isolated agentTurn` cron 推荐开启 `lightContext: true`：
- 减少冷启动成本
- 缩短 model-call 时间，降低 timeout 风险
- 例外：需要完整工具发现的复杂任务可关闭

### P3 · Prompt 必引用规则文件
所有生成持仓操作建议的 cron 必须在 prompt 开头加载规则：

```
【前置·规则加载】
exec cat docs/交易风控规则.md
exec cat docs/<其他相关规则>.md
```

并在末尾加风控自检（依《风控规则》R16）。

---

## 四、运维治理

### O1 · 修改前必先 get
任何 `cron action=update` 之前**必须**先 `cron action=get` 查看完整 payload，避免破坏其他字段。

### O2 · 错误恢复
- `consecutiveErrors >= 2` → failureAlert 自动推送
- 超过 3 次连续失败 → 必须人工排查，禁止简单"清错重启"
- timeout 是最常见错误，排查顺序：model-call 阶段超时 → prompt 过长 → 工具调用慢

### O3 · 禁止人工模拟 cron
不得使用 `exec sleep` 或 process 循环来"模拟 cron 调度"。所有定时任务必须通过 cron 工具。

### O4 · cron 日志保留
- `lastDiagnostics.entries` 中的失败原因必须定期查看
- 失败 cron 的 `lastError` + `lastErrorReason` 是排查的第一现场

---

## 五、新增/删除 cron 流程

### N1 · 新增 cron
1. 辉哥提出需求或 AI 提议 → **辉哥确认**
2. 起草 `cron action=add` 完整 payload（含 delivery + failureAlert）
3. **dry-run 思考**：触发时间是否合理？schedule 是否与其他 cron 冲突？timeout 是否够？
4. 调用 `cron action=add` 创建
5. 立即 `cron action=run` 触发一次验证
6. 验证通过 → 记录到 memory
7. 验证失败 → 立即 `cron action=remove` 或 `action=update enabled:false`

### N2 · 删除 cron
1. 辉哥明确"删除 XXX cron"
2. 先 `cron action=update enabled:false` 禁用
3. 观察 24h 无影响 → `cron action=remove` 永久删除
4. 记录到 memory

### N3 · 禁止操作
- 禁止 AI 自主新增 cron（必须辉哥确认）
- 禁止 AI 自主删除现有 cron（必须辉哥明确指令）
- 禁止用 `cron action=update enabled:false` 替代删除（应明确选用）

---

## 六、Cron 自检体检

### B1 · 系统体检触发条件
辉哥说"做下 cron 体检" → AI 必须：
1. `cron action=list`
2. 检查每个 cron 的：
   - delivery 是否配对（T1）
   - failureAlert 是否齐全（T2）
   - schedule 是否合理（S1/S2）
   - timeout 是否充足（S3）
   - consecutiveErrors 是否累积
3. 输出问题清单 + 修复建议

### B2 · 定期主动体检
建议每周末做一次 cron 体检（人工或自动 cron 触发）。

---

## 七、维护

- 本规则修订需辉哥确认
- 修订后必须同步更新 memory 中规则演进历史
- **禁止 AI 自主修改本文件**

---

## 变更日志

- **v1.0 (2026-06-06)** · 首次发版，整合十二轮治理事件经验 + 当前 26 cron 实战规范
