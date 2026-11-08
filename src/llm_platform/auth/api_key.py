"""API Key 生成/哈希/校验：明文仅创建时返回一次，库中只存 SHA-256 hash。"""

from __future__ import annotations

import secrets

from ..utils.masking import hash_value

KEY_PREFIX = "sk-"
KEY_BODY_LENGTH = 48  # 随机部分长度（hex）


def generate_api_key() -> tuple[str, str, str]:
    """生成 API Key。

    Returns:
        (plaintext_key, key_hash, key_prefix)
        plaintext_key: 明文，仅创建时返回一次
        key_hash: SHA-256 hex，入库
        key_prefix: 前 8 位，仅供展示识别
    """
    body = secrets.token_hex(KEY_BODY_LENGTH // 2)  # 24 bytes -> 48 hex chars
    plaintext = f"{KEY_PREFIX}{body}"
    key_hash = hash_value(plaintext)
    key_prefix = plaintext[:8]
    return plaintext, key_hash, key_prefix


def hash_api_key(plaintext: str) -> str:
    """计算 API Key 的 SHA-256 哈希。"""
    return hash_value(plaintext)
