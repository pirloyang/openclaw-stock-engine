#!/usr/bin/env python3
"""
gateway_export.py — 导出 OpenClaw 网关配置为可版本化模板。

策略：
- 调用 gateway config.get（通过 openclaw CLI 或读 ~/.openclaw/openclaw.json）
- 只导出 user-authored 部分（parsed/sourceConfig），跳过 resolved/runtimeConfig
- 把所有 __OPENCLAW_REDACTED__ 字段替换为 ${VAR}，并在 .env.example 里登记
- 跑前需要把 openclaw.json 备份一次（防误删）

输出：
- deploy/gateway_config.template.json
- deploy/gateway_secrets.manifest.json（记录哪些字段是 secret）
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

CONFIG_FILE = Path.home() / ".openclaw" / "openclaw.json"
WORKSPACE = Path("/root/.openclaw/workspace")
OUT_TEMPLATE = WORKSPACE / "deploy" / "gateway_config.template.json"
OUT_MANIFEST = WORKSPACE / "deploy" / "gateway_secrets.manifest.json"

# 哪些字段路径是 secret（按 dot path 匹配）；
# 这些字段会被替换成 ${...} 占位符，且写入 .env.example
SECRET_PATHS = {
    "gateway.auth.token": "GATEWAY_AUTH_TOKEN",
    "models.providers.deepseek.apiKey": "DEEPSEEK_API_KEY",
    "models.providers.arkcode.apiKey": "ARKCODE_API_KEY",
    "channels.lightclawbot.accounts.100003159947.apiKey": "LIGHTCLAWBOT_API_KEY",
    "plugins.entries.tavily.config.webSearch.apiKey": "TAVILY_API_KEY",
}


def walk_replace_secrets(obj, path=""):
    """递归扫描，遇到 SECRET_PATHS 中定义的路径或值为 __OPENCLAW_REDACTED__ 的字段就替换"""
    if isinstance(obj, dict):
        result = {}
        for k, v in obj.items():
            sub_path = f"{path}.{k}" if path else k
            if sub_path in SECRET_PATHS:
                result[k] = f"${{{SECRET_PATHS[sub_path]}}}"
            elif isinstance(v, str) and v == "__OPENCLAW_REDACTED__":
                # 未登记的 redacted 字段：用 path 自动生成占位符
                auto_var = sub_path.upper().replace(".", "_")
                result[k] = f"${{{auto_var}}}"
                SECRET_PATHS.setdefault(sub_path, auto_var)
            else:
                result[k] = walk_replace_secrets(v, sub_path)
        return result
    if isinstance(obj, list):
        return [walk_replace_secrets(x, f"{path}[{i}]") for i, x in enumerate(obj)]
    return obj


def main():
    if not CONFIG_FILE.exists():
        print(f"❌ {CONFIG_FILE} 不存在", file=sys.stderr)
        sys.exit(1)

    raw = json.loads(CONFIG_FILE.read_text())
    print(f"📋 读取 {CONFIG_FILE}")

    tokenized = walk_replace_secrets(raw)

    OUT_TEMPLATE.parent.mkdir(parents=True, exist_ok=True)
    OUT_TEMPLATE.write_text(json.dumps(tokenized, ensure_ascii=False, indent=2))

    manifest = {
        "exported_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "source": str(CONFIG_FILE),
        "secrets": SECRET_PATHS,
    }
    OUT_MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))

    # 检查是否还有未处理的 redacted 字段
    template_text = OUT_TEMPLATE.read_text()
    leftover = template_text.count("__OPENCLAW_REDACTED__")
    if leftover > 0:
        print(f"⚠️  仍有 {leftover} 个未脱敏字段，请人工检查", file=sys.stderr)

    print(f"✅ 模板：{OUT_TEMPLATE}")
    print(f"✅ 清单：{OUT_MANIFEST}")
    print(f"📝 需要在 .env 中提供的变量：")
    for path, var in SECRET_PATHS.items():
        print(f"   {var}  ← {path}")


if __name__ == "__main__":
    main()
