# deploy/ — 部署与可复现性

把网关运行态的配置（主要是 cron jobs）版本化，让股票量化系统能一键部署到新机器。

## 为什么需要

- OpenClaw 网关把 cron 存在 `~/.openclaw/cron/jobs.json`（节点级运行态），**不在 git 跟踪范围**
- 新机器装好 OpenClaw 后，需要 26+ 个 cron 任务才能正常运行盘前/盘中/盘后流水线
- 凭手工 `cron add` 重建 → 易漏、易错、敏感数据（微信 openid）会泄漏到代码

## 设计

```
源头（运行态）              中间态（版本化）           目标（运行态）
~/.openclaw/cron/jobs.json  →  deploy/cron_templates/  →  ~/.openclaw/cron/jobs.json
                               deploy/cron_manifest.json
                               deploy/.env              （不进 git）
       cron_export.py              git pull              cron_deploy.py
```

**敏感数据隔离**：
- `cron_templates/*.json` — 把 openid/accountId/workspace 路径替换成 `${VAR}` 占位符 → ✅ 进 git
- `.env` — 真实的 openid、accountId、workspace 路径 → ❌ `.gitignore`
- `.env.example` — 字段说明模板 → ✅ 进 git

## 日常用法（在当前机器）

### 任何 cron 改动后，重新导出
```bash
python3 deploy/cron_export.py
git add deploy/cron_templates deploy/cron_manifest.json
git commit -m "cron: 同步快照"
```

### 推荐做法：让 cron 改动自动同步
建议在 cron 治理规则里加一条「凡是改 cron schedule/payload，最后一步必须跑 export」。
未来可以加个 git hook 或日终 cron 自动 export。

## 新机器部署流程

```bash
# 1. 装 OpenClaw（略），把 workspace 仓库 clone 下来
cd ~/.openclaw && git clone <repo> workspace

# 2. 装依赖（akshare/playwright/...）
cd workspace && bash scripts/install_deps.sh   # 待补

# 3. 配置敏感数据
cp deploy/.env.example deploy/.env
vim deploy/.env   # 填入新机器的微信 openid 等

# 4. 预览要部署的 cron
python3 deploy/cron_deploy.py --dry-run

# 5. 生成部署动作清单（让 AI 助手或脚本执行）
python3 deploy/cron_deploy.py --apply

# 6. AI 助手读 deploy/cron_deploy_actions.json，调 cron add / cron update 批量执行
```

## 文件清单

| 文件 | 作用 | 进 git? |
|---|---|---|
| `cron_export.py` | 从网关导出模板 | ✅ |
| `cron_deploy.py` | 计算部署动作 | ✅ |
| `cron_templates/*.json` | 参数化 job 定义 | ✅ |
| `cron_manifest.json` | 汇总清单 + token 字典 | ✅ |
| `.env.example` | 环境变量样板 | ✅ |
| `.env` | 真实环境变量 | ❌ gitignore |
| `cron_deploy_actions.json` | 部署一次性产物 | ❌ gitignore |
| `README.md` | 本文件 | ✅ |

## 当前覆盖范围

只覆盖了 **cron 任务**。其他需要可复现的部分（待补）：
- [ ] OpenClaw 网关 config（`gateway config.get/apply` 工具能力）
- [ ] Python 依赖（requirements.txt）
- [ ] 系统依赖（apt/curl 安装项）
- [ ] 工作区初始目录结构（stock-signals/, sim_trading/, /tmp/stock_alerts 等）

后续随项目产品化逐步补齐。

## 反例 / 不要做的事

- ❌ 直接 `git add ~/.openclaw/cron/jobs.json` — 含敏感数据、运行态
- ❌ 在 cron prompt 里硬编码绝对路径，要走 `${WORKSPACE_ROOT}` 占位符
- ❌ 手工 `cron add` 而不更新模板 — 下次部署会丢
