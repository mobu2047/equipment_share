from __future__ import annotations

"""
鉴权服务：短信登录（开发模式）、刷新/登出、角色管理

注意：
- 刷新令牌落库并支持旋转；
- 仅最小实现，未接入短信落库，生产应完善审计与风控。
"""

import uuid
from datetime import timedelta
from typing import List

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from backend.core.config import get_settings
from backend.core.security import create_access_token, generate_refresh_token, hash_token, now_utc
from backend.models.user import User, Role, UserRole, RefreshSession
from backend.services.sms_gateway import SMSGateway


class AuthService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()
        self.sms = SMSGateway()

    async def send_sms(self, *, phone: str, scene: str) -> dict:
        return await self.sms.send_code(phone=phone, scene=scene)

    async def verify_login(self, *, phone: str, code: str) -> tuple[str, str, str]:
        ok = await self.sms.verify_code(phone=phone, code=code, scene="login")
        if not ok:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="验证码错误")

        # 找或创建用户（默认 TENANT）
        user = self.db.query(User).filter(User.phone == phone).first()
        if not user:
            user = User(phone=phone, display_name=phone)
            self.db.add(user)
            self.db.flush()
            # 绑定 TENANT 角色
            tenant_role = self._get_or_create_role("TENANT")
            self.db.add(UserRole(user_id=user.id, role_id=tenant_role.id))
            self.db.flush()

        # 若为默认管理员手机号，则确保授予 ADMIN 角色
        try:
            if phone == self.settings.admin_default_phone:
                admin_role = self._get_or_create_role("ADMIN")
                # 检查是否已有
                has_admin = (
                    self.db.query(UserRole)
                    .filter(UserRole.user_id == user.id, UserRole.role_id == admin_role.id)
                    .first()
                    is not None
                )
                if not has_admin:
                    self.db.add(UserRole(user_id=user.id, role_id=admin_role.id))
                    self.db.flush()
        except Exception:
            # 若赋权失败不影响登录
            pass

        # 以查询方式获取角色，避免关系未加载
        roles = [r.name for r in user.roles] if getattr(user, "roles", None) else [
            row[0] for row in self.db.query(Role.name).join(UserRole, Role.id == UserRole.role_id).filter(UserRole.user_id == user.id).all()
        ]

        # 生成 access
        access = create_access_token(user_id=user.id, roles=roles)

        # 生成 refresh 会话
        refresh_plain = generate_refresh_token()
        session_id = uuid.uuid4().hex
        rs = RefreshSession(
            id=session_id,
            user_id=user.id,
            token_hash=hash_token(refresh_plain),
            created_at=now_utc(),
            expires_at=now_utc() + timedelta(days=self.settings.refresh_expires_days),
        )
        self.db.add(rs)
        # 提交事务，确保用户/角色/会话写入
        self.db.commit()

        return access, refresh_plain, session_id

    def _get_or_create_role(self, name: str) -> Role:
        r = self.db.query(Role).filter(Role.name == name).first()
        if r:
            return r
        r = Role(name=name, description=name.title())
        self.db.add(r)
        self.db.flush()
        return r

    def rotate_refresh(self, *, current_session_id: str, current_refresh: str) -> tuple[str, str, str, int]:
        # 通过 session_id 找到记录并校验
        rec = self.db.query(RefreshSession).filter(RefreshSession.id == current_session_id).first()
        if not rec or rec.revoked_at is not None or rec.expires_at < now_utc():
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="刷新会话无效")
        if rec.token_hash != hash_token(current_refresh):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="刷新令牌不匹配")

        # 旋转 -> 生成新会话
        new_refresh = generate_refresh_token()
        new_session_id = uuid.uuid4().hex
        new_rec = RefreshSession(
            id=new_session_id,
            user_id=rec.user_id,
            token_hash=hash_token(new_refresh),
            created_at=now_utc(),
            expires_at=now_utc() + timedelta(days=self.settings.refresh_expires_days),
        )
        self.db.add(new_rec)

        # 吊销旧会话
        rec.revoked_at = now_utc()
        rec.replaced_by_session_id = new_session_id

        # 生成新的 access
        user = self.db.get(User, rec.user_id)
        roles = [r.name for r in user.roles] if user else []
        access = create_access_token(user_id=rec.user_id, roles=roles)
        # 提交旋转
        self.db.commit()
        return access, new_refresh, new_session_id, rec.user_id

    def revoke_current(self, *, session_id: str) -> None:
        rec = self.db.query(RefreshSession).filter(RefreshSession.id == session_id).first()
        if rec and rec.revoked_at is None:
            rec.revoked_at = now_utc()
            self.db.commit()

    def revoke_all(self, *, user_id: int) -> int:
        q = self.db.query(RefreshSession).filter(RefreshSession.user_id == user_id, RefreshSession.revoked_at.is_(None))
        count = 0
        for rec in q:  # type: ignore[assignment]
            rec.revoked_at = now_utc()
            count += 1
        self.db.commit()
        return count


