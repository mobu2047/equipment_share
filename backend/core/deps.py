"""
FastAPI 依赖：当前用户与角色校验

职责：
- 从 Authorization: Bearer 中解析 Access Token；
- 解码 JWT，加载用户基础信息；
- 提供 require_roles 依赖用于 RBAC。
"""

from __future__ import annotations

from typing import Annotated, List

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from backend.core.db import get_db
from backend.core.security import decode_jwt
from backend.models.user import User


http_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(http_bearer)],
    db: Session = Depends(get_db),
) -> User:
    """从 Bearer Token 解析并返回当前用户。"""
    if creds is None or not creds.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing access token")
    try:
        payload = decode_jwt(creds.credentials)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token")

    if payload.get("typ") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")

    user = db.get(User, int(user_id)) if isinstance(user_id, str) and user_id.isdigit() else db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
    return user


def require_roles(*roles: str):
    async def _check(user: User = Depends(get_current_user)) -> User:
        role_names = {r.name for r in user.roles}
        if not any(r in role_names for r in roles):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
        return user

    return _check


