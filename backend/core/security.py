"""
安全与令牌工具

功能：
- 生成/校验 JWT（HS256）；
- 刷新令牌明文的随机生成与哈希；
- 通用时间与 ID 工具。

注意：
- 刷新令牌的持久化与旋转逻辑在 service 层实现；
- 本模块不直接依赖 ORM，避免循环引用。
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import jwt

from backend.core.config import get_settings


def now_utc() -> datetime:
    """返回当前 UTC 时间（aware）。"""
    return datetime.now(timezone.utc)


def generate_refresh_token() -> str:
    """生成高熵刷新令牌明文。"""
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    """将令牌以 SHA-256 哈希为十六进制字符串存库，避免明文落库。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def encode_jwt(payload: Dict[str, Any], expires_delta: timedelta) -> str:
    """生成 HS256 JWT，自动补充 iat/exp。"""
    settings = get_settings()
    to_encode = dict(payload)
    issued_at = now_utc()
    expire_at = issued_at + expires_delta
    to_encode.update({
        "iat": int(issued_at.timestamp()),
        "exp": int(expire_at.timestamp()),
    })
    return jwt.encode(to_encode, settings.jwt_secret, algorithm="HS256")


def decode_jwt(token: str) -> Dict[str, Any]:
    """校验并解码 JWT，返回 payload。失败将抛出 jwt 异常。"""
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])  # type: ignore[no-any-return]


def create_access_token(*, user_id: int | str, roles: list[str]) -> str:
    """为用户创建短期访问令牌。"""
    settings = get_settings()
    return encode_jwt(
        {"sub": str(user_id), "roles": roles, "typ": "access"},
        expires_delta=timedelta(minutes=settings.access_expires_minutes),
    )


def create_refresh_token_payload(*, session_id: str, user_id: int | str) -> str:
    """创建绑定会话 ID 的刷新令牌（明文）。

    说明：
    - 实际刷新令牌仍用随机明文（generate_refresh_token），此处仅提供一个可选 JWT 形式；
    - 当前实现未使用该函数，保留作扩展（例如跨域多端处理）。
    """
    settings = get_settings()
    return encode_jwt(
        {"sid": session_id, "sub": str(user_id), "typ": "refresh"},
        expires_delta=timedelta(days=settings.refresh_expires_days),
    )


