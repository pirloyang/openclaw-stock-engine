#!/usr/bin/env python3
"""
cron_export.py — 把 OpenClaw 网关运行态的 cron jobs 导出为可版本化模板。

策略：
- 从 ~/.openclaw/cron/jobs.json 读全部 job
- 剥离运行态字段（id/createdAtMs/state/lastRunXxx/nextRunAtMs）
- 把账号/收件人/路径参数化为 ${VAR}
- 按 name 排序，写到 deploy/cron_templates/<safe_name>.json
- 同时生成 deploy/cron_manifest.json（job 总清单 + 部署元信息）

部署侧用 cron_deploy.py 配合 .env 复原。
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

# ── 配置 ──────────────────────────────────────────────
JOBS_FILE = Path.home() / ".openclaw" / "cron" / "jobs.json"
WORKSPACE = Path("/root/.openclaw/workspace")
OUT_DIR = WORKSPACE / "deploy" / "cron_templates"
MANIFEST = WORKSPACE / "deploy" / "cron_manifest.json"

# 运行态字段（不导出）
RUNTIME_FIELDS = {
    "id", "createdAtMs", "updatedAtMs", "state",
    "lastRunAtMs", "lastRunStatus", "lastDurationMs",
    "lastDeliveryStatus", "lastError", "lastDiagnostics",
    "lastDiagnosticSummary", "lastErrorReason",
    "consecutiveErrors", "consecutiveSkipped",
    "lastFailureNotificationDeliveryStatus", "lastDelivered",
    "nextRunAtMs",
}

# 敏感字段参数化映射
TOKEN_REPLACEMENTS = [
    # 微信 openid（用户）
    ("o9cq80z60LaB2jyf8JO9xNWsynN4@im.wechat", "${WECHAT_USER_ID}"),
    # bot accountId
    ("75c2b3e86437-im-bot", "${WECHAT_BOT_ACCOUNT_ID}"),
    # 工作区根路径
    ("/root/.openclaw/workspace", "${WORKSPACE_ROOT}"),
]


def sanitize_name(name: str) -> str:
    """job name → 文件名安全形式"""
    safe = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", name).strip("_")
    return safe[:80] or "unnamed"


def strip_runtime(obj):
    """递归剥离运行态字段"""
    if isinstance(obj, dict):
        return {k: strip_runtime(v) for k, v in obj.items() if k not in RUNTIME_FIELDS}
    if isinstance(obj, list):
        return [strip_runtime(x) for x in obj]
    return obj


def tokenize(obj):
    """把敏感字符串替换为占位符"""
    if isinstance(obj, dict):
        return {k: tokenize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [tokenize(x) for x in obj]
    if isinstance(obj, str):
        s = obj
        for raw, tok in TOKEN_REPLACEMENTS:
            s = s.replace(raw, tok)
        return s
    return obj


def normalize(job: dict) -> dict:
    """完整规范化：去运行态 → 参数化"""
    job = strip_runtime(job)
    job = tokenize(job)
    # 不导出 delivery.failureDestination 这类很少用的次级字段（如果空）
    return job


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--include-disabled", action="store_true",
                    help="也导出禁用的 job（默认只导出 enabled=true 的）")
    args = ap.parse_args()

    if not JOBS_FILE.exists():
        print(f"❌ {JOBS_FILE} 不存在", file=sys.stderr)
        sys.exit(1)

    data = json.loads(JOBS_FILE.read_text())
    all_jobs = data["jobs"] if isinstance(data, dict) else data
    if args.include_disabled:
        jobs = all_jobs
        print(f"📋 读到 {len(jobs)} 个 job (含禁用) 来源：{JOBS_FILE}")
    else:
        jobs = [j for j in all_jobs if j.get("enabled", True)]
        skipped = len(all_jobs) - len(jobs)
        print(f"📋 读到 {len(jobs)} 个启用 job (跳过 {skipped} 个禁用) 来源：{JOBS_FILE}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 清空旧模板
    for f in OUT_DIR.glob("*.json"):
        f.unlink()

    manifest = {
        "exported_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "workspace": str(WORKSPACE),
        "jobs_file": str(JOBS_FILE),
        "total_jobs": 0,
        "enabled_jobs": 0,
        "disabled_jobs": 0,
        "jobs": [],
        "tokens": {tok: raw for raw, tok in TOKEN_REPLACEMENTS},
        "env_template": [tok.strip("${}") for _, tok in TOKEN_REPLACEMENTS],
    }

    seen_names = {}
    for raw in jobs:
        clean = normalize(raw)
        name = clean.get("name", "unnamed")
        safe = sanitize_name(name)
        # 重名去重
        if safe in seen_names:
            seen_names[safe] += 1
            safe = f"{safe}_{seen_names[safe]}"
        else:
            seen_names[safe] = 1

        out_path = OUT_DIR / f"{safe}.json"
        out_path.write_text(json.dumps(clean, ensure_ascii=False, indent=2))

        manifest["jobs"].append({
            "name": name,
            "file": f"cron_templates/{out_path.name}",
            "enabled": clean.get("enabled", True),
            "schedule": clean.get("schedule"),
            "sessionTarget": clean.get("sessionTarget"),
            "payload_kind": clean.get("payload", {}).get("kind"),
            "delivery_mode": clean.get("delivery", {}).get("mode"),
            "description": clean.get("description", ""),
        })

        manifest["total_jobs"] += 1
        if clean.get("enabled", True):
            manifest["enabled_jobs"] += 1
        else:
            manifest["disabled_jobs"] += 1

    # 按 name 排序
    manifest["jobs"].sort(key=lambda j: j["name"])

    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"✅ 导出完成：{manifest['total_jobs']} 个 job (启用 {manifest['enabled_jobs']} / 禁用 {manifest['disabled_jobs']})")
    print(f"   模板目录：{OUT_DIR}")
    print(f"   清单：{MANIFEST}")


if __name__ == "__main__":
    main()
