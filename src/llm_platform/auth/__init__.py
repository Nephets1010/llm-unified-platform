"""鉴权与审计模块：fail-closed 双轨鉴权（API Key + JWT）。"""

from .api_key import generate_api_key, hash_api_key
from .audit import audit_log
from .jwt import create_access_token, decode_access_token
from .service import AuthContext, AuthService

__all__ = [
    "create_access_token",
    "decode_access_token",
    "generate_api_key",
    "hash_api_key",
    "AuthService",
    "AuthContext",
    "audit_log",
]
