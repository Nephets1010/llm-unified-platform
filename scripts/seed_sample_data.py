#!/usr/bin/env python3
"""种子数据脚本：创建示例租户/Key/模型端点。

用法：
    python scripts/seed_sample_data.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from llm_platform.auth.api_key import generate_api_key
from llm_platform.db.repositories.endpoint_repo import EndpointRepo
from llm_platform.db.repositories.key_repo import KeyRepo
from llm_platform.db.repositories.tenant_repo import TenantRepo
from llm_platform.db.session import SessionLocal, init_db


def seed() -> None:
    init_db()
    db = SessionLocal()
    try:
        tenant_repo = TenantRepo(db)
        key_repo = KeyRepo(db)
        endpoint_repo = EndpointRepo(db)

        # ── 租户 ──
        tenant = tenant_repo.create_tenant("demo-tenant", monthly_budget=5000.0)
        print(f"[OK] 租户: id={tenant.id} name={tenant.name}")

        # ── 项目 ──
        project = tenant_repo.create_project(tenant.id, "demo-project", monthly_budget=2000.0)
        print(f"[OK] 项目: id={project.id} name={project.name}")

        # ── API Key ──
        plaintext, key_hash, key_prefix = generate_api_key()
        key = key_repo.create_key(
            key_hash=key_hash,
            key_prefix=key_prefix,
            tenant_id=tenant.id,
            project_id=project.id,
            name="demo-key",
            scopes=["chat", "embeddings", "admin"],
            rpm_limit=120,
            tpm_limit=120000,
        )
        print(f"[OK] API Key: id={key.id} prefix={key_prefix}")
        print(f"     明文（仅此一次！）: {plaintext}")

        # ── 模型端点 ──
        endpoints = [
            {
                "provider": "openai",
                "protocol": "openai",
                "model_name": "gpt-4o-mini",
                "base_url": "https://api.openai.com/v1",
                "credential_env": "LLM_OPENAI_API_KEY",
                "cost_per_1k_prompt": 0.00015,
                "cost_per_1k_completion": 0.0006,
            },
            {
                "provider": "deepseek",
                "protocol": "openai",
                "model_name": "deepseek-chat",
                "base_url": "https://api.deepseek.com/v1",
                "credential_env": "LLM_DEEPSEEK_API_KEY",
                "cost_per_1k_prompt": 0.0002,
                "cost_per_1k_completion": 0.001,
            },
            {
                "provider": "qwen",
                "protocol": "openai",
                "model_name": "qwen-plus",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "credential_env": "LLM_QWEN_API_KEY",
                "cost_per_1k_prompt": 0.0008,
                "cost_per_1k_completion": 0.002,
            },
            {
                "provider": "ollama",
                "protocol": "ollama",
                "model_name": "llama3",
                "base_url": "http://localhost:11434",
                "credential_env": "LLM_OLLAMA_BASE_URL",
                "cost_per_1k_prompt": 0.0,
                "cost_per_1k_completion": 0.0,
            },
        ]

        for ep_data in endpoints:
            ep = endpoint_repo.create_endpoint(**ep_data)
            print(f"[OK] 端点: id={ep.id} provider={ep.provider} model={ep.model_name}")

        print("\n✅ 种子数据创建完成！")
        print(f"   API Key 明文: {plaintext}")
        print("   请妥善保管此 Key，后续查询只显示前缀。")

    finally:
        db.close()


if __name__ == "__main__":
    seed()
