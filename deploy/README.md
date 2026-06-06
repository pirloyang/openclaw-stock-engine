# deploy/ — 部署与可复现性

把网关运行态的配置（cron、gateway config）和环境依赖版本化，让股票量化系统能一键部署到新机器。

## 为什么需要

- OpenClaw 网关把 cron 存在 `~/.openclaw/cron/jobs.json`（节点级运行态），**不在 git 跟踪范围**
- 网关 config 在 `~/.openclaw/openclaw.json`，含 API Key 等敏感字段，**不能直接 git**
- 新机器装好 OpenClaw 后，需要 25 个 cron 任务 + 多组 API Key 才能正常跑量化系统
- 凭手工重建 → 易漏、易错、敏感数据泄漏到代码

## 设计

```
源头（运行态）                          中间态（版本化）                目标（运行态）
~/.openclaw/cron/jobs.json         →  deploy/cron_templates/      →  ~/.openclaw/cron/jobs.json
~/.openclaw/openclaw.json          →  deploy/gateway_config.tpl   →  ~/.openclaw/openclaw.json
                                      deploy/.env (本地)
   cron_export.py / gateway_export.py     git pull           cron_deploy.py
```

**敏感数据隔离**：
- `cron_templates/*.json`、`gateway_config.template.json` — 占位符版 → ✅ 进 git
- `.env` — 真实的 openid、API Keys → ❌ `.gitignore`
- `.env.example` — 字段说明模板 → ✅ 进 git

## 日常用法（在当前机器）

### Cron 改动后，重新导出
```bash
python3 deploy/cron_export.py
git add deploy/cron_templates deploy/cron_manifest.json
git commit -m "cron: 同步快照"
```

### Gateway config 改动后
```bash
python3 deploy/gateway_export.py
git add deploy/gateway_config.template.json deploy/gateway_secrets.manifest.json
git commit -m "gateway: 同步配置"
```

**建议**：在 cron 治理规则里加一条「凡是改 cron schedule/payload 或网关 config，最后一步必须跑 export」，未来可加日终 cron 自动 export。

## 新机器部署流程

```bash
# ── 阶段 1：环境准备 ──
# 1. 装 OpenClaw 网关（见官方文档）
# 2. clone workspace
cd ~/.openclaw && git clone <repo> workspace && cd workspace

# 3. 安装系统依赖（apt + playwright + pnpm）
bash scripts/install_deps.sh

# 4. 初始化目录结构 + 校验
bash scripts/init_workspace.sh

# ── 阶段 2：恢复敏感数据 ──
# 5. 填环境变量
cp deploy/.env.example deploy/.env
vim deploy/.env   # WECHAT_USER_ID / DEEPSEEK_API_KEY / ARKCODE_API_KEY / TAVILY_API_KEY

# 6. （可选）从备份恢复 TOOLS.md（持仓数据）
#    无备份则需手动重建持仓清单

# ── 阶段 3：部署 cron + gateway ──
# 7. 预览 cron 部署
python3 deploy/cron_deploy.py --dry-run

# 8. 生成部署动作清单
python3 deploy/cron_deploy.py --apply
# 把 deploy/cron_deploy_actions.json 交给 AI 助手或脚本批量执行 cron add/update

# 9. （可选）部署 gateway config 改动
#    手工对照 deploy/gateway_config.template.json 与 ~/.openclaw/openclaw.json
#    或用 gateway config.patch 工具按需注入

# 10. 重启网关
openclaw gateway restart

# ── 阶段 4：自检 ──
# 11. 跑一次规则体检
python3 scripts/rules_audit.py
```

## 文件清单

| 文件 | 作用 | 进 git? |
|---|---|---|
| `cron_export.py` | 从网关导出 cron 模板 | ✅ |
| `cron_deploy.py` | 计算 cron 部署动作 | ✅ |
| `cron_templates/*.json` | 25 个脱敏 cron 模板 | ✅ |
| `cron_manifest.json` | 汇总清单 + token 字典 | ✅ |
| `gateway_export.py` | 导出网关 config 模板 | ✅ |
| `gateway_config.template.json` | 网关配置脱敏模板 | ✅ |
| `gateway_secrets.manifest.json` | 敏感字段→变量映射 | ✅ |
| `.env.example` | 环境变量样板 | ✅ |
| `.env` | 真实环境变量（含 API Keys） | ❌ gitignore |
| `cron_deploy_actions.json` | 部署一次性产物 | ❌ gitignore |
| `README.md` | 本文件 | ✅ |
| `requirements.txt`（在仓库根） | Python 依赖 | ✅ |
| `scripts/install_deps.sh` | 系统依赖一键装 | ✅ |
| `scripts/init_workspace.sh` | 目录结构初始化 | ✅ |

## 当前覆盖范围

| 项 | 状态 |
|---|---|
| Cron 任务 | ✅ 25 个全覆盖 |
| Gateway config | ✅ 含 5 个 API Key 占位符 |
| Python 依赖 | ✅ requirements.txt |
| 系统依赖（apt/playwright） | ✅ install_deps.sh |
| 工作区目录结构 | ✅ init_workspace.sh |
| 个人持仓（TOOLS.md） | ❌ 故意不版本化（敏感）|
| 行情缓存（stock-signals/cache/） | ❌ 故意不版本化（运行时数据，可重建）|
| 报告（reports/） | ❌ 故意不版本化（每日产物）|

## 反例 / 不要做的事

- ❌ 直接 `git add ~/.openclaw/cron/jobs.json` — 含敏感数据、运行态
- ❌ 直接 `git add ~/.openclaw/openclaw.json` — 含 API Keys
- ❌ 在 cron prompt 里硬编码绝对路径，要走 `${WORKSPACE_ROOT}` 占位符
- ❌ 手工 `cron add` 而不更新模板 — 下次部署会丢
- ❌ 把 .env 提交进 git
