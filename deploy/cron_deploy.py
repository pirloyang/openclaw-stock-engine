#!/usr/bin/env python3
"""
cron_deploy.py — 把模板部署到 OpenClaw 网关。

用法：
    cp deploy/.env.example deploy/.env  # 填入新机器的 WECHAT_USER_ID 等
    python3 deploy/cron_deploy.py --dry-run     # 预览
    python3 deploy/cron_deploy.py --apply       # 实际写入

策略：
- 读取 deploy/.env 加载占位符变量
- 读取 deploy/cron_manifest.json
- 对每个模板：
    1) 把 ${VAR} 替换回真实值
    2) 看网关里是否已有同名 job
       - 没有 → 新建（cron add）
       - 已有 → 比对 schedule/payload/delivery，不同则更新
- 输出 deploy/cron_deploy_report.<ts>.json

注意：不会删除网关多出的 job（防误删），只新增/更新。
如要清理，给 --prune 参数。

依赖：通过 openclaw CLI 调用 gateway（已有）；或直接 HTTP 调用。
本脚本简化：生成一份"部署作业列表 JSON"，由调用方传给 openclaw cron 工具。
"""
from __future__ import annotations
import json
import os
import re
import sys
import argparse
from pathlib import Path
from datetime import datetime, timezone, timedelta

WORKSPACE = Path("/root/.openclaw/workspace")
DEPLOY_DIR = WORKSPACE / "deploy"
ENV_FILE = DEPLOY_DIR / ".env"
MANIFEST = DEPLOY_DIR / "cron_manifest.json"
JOBS_FILE = Path.home() / ".openclaw" / "cron" / "jobs.json"


def load_env() -> dict:
    """加载 deploy/.env，返回 dict"""
    if not ENV_FILE.exists():
        print(f"❌ 缺少 {ENV_FILE}，先复制 .env.example", file=sys.stderr)
        sys.exit(1)
    env = {}
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def substitute(obj, env: dict):
    """把 ${VAR} 替换为真实值"""
    if isinstance(obj, dict):
        return {k: substitute(v, env) for k, v in obj.items()}
    if isinstance(obj, list):
        return [substitute(x, env) for x in obj]
    if isinstance(obj, str):
        def replace(m):
            key = m.group(1)
            if key not in env:
                raise KeyError(f"缺少环境变量 {key}（在 deploy/.env 中定义）")
            return env[key]
        return re.sub(r"\$\{(\w+)\}", replace, obj)
    return obj


def load_existing_jobs() -> dict:
    """从网关读现有 jobs（按 name 索引）"""
    if not JOBS_FILE.exists():
        return {}
    data = json.loads(JOBS_FILE.read_text())
    jobs = data["jobs"] if isinstance(data, dict) else data
    return {j["name"]: j for j in jobs}


def diff_significant(existing: dict, template: dict) -> list[str]:
    """返回需更新的字段列表"""
    diffs = []
    for key in ("enabled", "schedule", "sessionTarget", "wakeMode",
                "payload", "delivery", "failureAlert", "description",
                "agentId", "deleteAfterRun"):
        if existing.get(key) != template.get(key):
            diffs.append(key)
    return diffs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只预览，不写入")
    ap.add_argument("--apply", action="store_true", help="实际写入网关 (生成 actions.json 由外层 openclaw cron 调用)")
    ap.add_argument("--prune", action="store_true", help="允许删除模板里没有的 job (危险)")
    ap.add_argument("--output", default=str(DEPLOY_DIR / "cron_deploy_actions.json"))
    args = ap.parse_args()

    if not args.dry_run and not args.apply:
        print("❌ 必须指定 --dry-run 或 --apply", file=sys.stderr)
        sys.exit(2)

    env = load_env()
    manifest = json.loads(MANIFEST.read_text())
    existing = load_existing_jobs()

    actions = {"add": [], "update": [], "skip": [], "prune": []}
    template_names = set()

    for item in manifest["jobs"]:
        tpl_path = DEPLOY_DIR / item["file"]
        if not tpl_path.exists():
            print(f"⚠️  模板缺失：{tpl_path}")
            continue
        template = json.loads(tpl_path.read_text())
        try:
            template = substitute(template, env)
        except KeyError as e:
            print(f"❌ 模板 {item['name']} 缺变量: {e}", file=sys.stderr)
            sys.exit(3)

        name = template["name"]
        template_names.add(name)

        if name not in existing:
            actions["add"].append({"name": name, "job": template})
            print(f"➕ ADD    {name}")
        else:
            diffs = diff_significant(existing[name], template)
            if diffs:
                actions["update"].append({
                    "name": name,
                    "jobId": existing[name]["id"],
                    "patch": {k: template[k] for k in diffs if k in template},
                    "fields": diffs,
                })
                print(f"🔄 UPDATE {name}  字段: {','.join(diffs)}")
            else:
                actions["skip"].append({"name": name})

    if args.prune:
        for name, job in existing.items():
            if name not in template_names:
                actions["prune"].append({"name": name, "jobId": job["id"]})
                print(f"🗑️  PRUNE  {name}")

    summary = {
        "generated_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "add_count": len(actions["add"]),
        "update_count": len(actions["update"]),
        "skip_count": len(actions["skip"]),
        "prune_count": len(actions["prune"]),
        "actions": actions,
    }

    out_path = Path(args.output)
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2))

    print()
    print(f"📊 总结：新增 {summary['add_count']} / 更新 {summary['update_count']} / 跳过 {summary['skip_count']} / 待删 {summary['prune_count']}")
    print(f"📄 操作清单：{out_path}")

    if args.dry_run:
        print()
        print("💡 这是预览。如要实际执行，请把 actions.json 交给 openclaw cron 工具批量执行。")
        print("   也可让 AI 助手读取此文件，逐条调用 cron add/update。")


if __name__ == "__main__":
    main()
