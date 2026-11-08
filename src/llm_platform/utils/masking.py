"""敏感字段脱敏工具：日志与 DB 中不得出现明文 Key/Prompt/密钥。

规则：
- API Key / 密钥类字符串：只保留前 8 位 + 长度，中间用 *** 代替
- Prompt / 长文本：只记 SHA-256 hash + 长度
- dict 递归脱敏指定字段名
"""

from __future__ import annotations

import hashlib
from typing import Any

# 需脱敏的字段名（不区分大小写匹配）
_SENSITIVE_KEYS: set[str] = {
    "key", "api_key", "apikey", "secret", "token", "password", "credential",
    "authorization", "auth", "prompt", "content", "email", "phone",
}


def mask_secret(value: str, keep: int = 8) -> str:
    """脱敏密钥类字符串：保留前 keep 位，其余用 *** 代替，附带长度。

    示例: "sk-abcdef1234567890" -> "sk-abcde*** (len=18)"
    """
    if not value:
        return "*** (len=0)"
    if len(value) <= keep:
        return f"{'*' * len(value)} (len={len(value)})"
    return f"{value[:keep]}*** (len={len(value)})"


def hash_value(value: str) -> str:
    """返回 SHA-256 hex 摘要（用于 prompt_hash / key_hash）。"""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def mask_dict(data: dict[str, Any], extra_keys: set[str] | None = None) -> dict[str, Any]:
    """递归脱敏 dict 中的敏感字段。

    - 字段名命中 _SENSITIVE_KEYS 或 extra_keys → mask_secret
    - 嵌套 dict 递归处理
    - list 中的 dict 递归处理
    """
    keys = _SENSITIVE_KEYS | (extra_keys or set())
    result: dict[str, Any] = {}
    for k, v in data.items():
        if k.lower() in keys and isinstance(v, str):
            result[k] = mask_secret(v)
        elif isinstance(v, dict):
            result[k] = mask_dict(v, extra_keys)
        elif isinstance(v, list):
            result[k] = [
                mask_dict(item, extra_keys) if isinstance(item, dict) else item for item in v
            ]
        else:
            result[k] = v
    return result
