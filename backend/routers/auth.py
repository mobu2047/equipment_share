from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, Response, Request, HTTPException, status
from sqlalchemy.orm import Session

from backend.core.db import get_db
from backend.core.deps import get_current_user
from backend.core.logger import logger
from backend.core.security import decode_jwt
from backend.models.schemas import SMSRequest, SMSVerifyRequest, TokenResponse, UserInfo
from backend.services.auth_service import AuthService


router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/sms/send", response_model=Dict[str, Any])
async def send_sms(req: SMSRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    svc = AuthService(db)
    res = await svc.send_sms(phone=req.phone, scene=req.scene)
    logger.info("auth.sms.send", extra={"event": "sms_send", "phone": req.phone, "scene": req.scene})
    return res


@router.post("/verify", response_model=TokenResponse)
async def verify_login(req: SMSVerifyRequest, response: Response, db: Session = Depends(get_db)) -> TokenResponse:
    svc = AuthService(db)
    access, refresh, session_id = await svc.verify_login(phone=req.phone, code=req.code)
    # 写入 HttpOnly Cookie
    response.set_cookie("refresh_token", refresh, httponly=True, samesite="lax")
    response.set_cookie("session_id", session_id, httponly=True, samesite="lax")
    logger.info("auth.verify.ok", extra={"event": "verify_ok", "phone": req.phone})
    return TokenResponse(access_token=access)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(request: Request, response: Response, db: Session = Depends(get_db)) -> TokenResponse:
    cookies = request.cookies
    refresh = cookies.get("refresh_token")
    session_id = cookies.get("session_id")
    if not refresh or not session_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少刷新凭据")
    svc = AuthService(db)
    access, new_refresh, new_session_id, user_id = svc.rotate_refresh(current_session_id=session_id, current_refresh=refresh)
    response.set_cookie("refresh_token", new_refresh, httponly=True, samesite="lax")
    response.set_cookie("session_id", new_session_id, httponly=True, samesite="lax")
    logger.info("auth.refresh", extra={"event": "refresh", "user_id": user_id})
    return TokenResponse(access_token=access)


@router.post("/logout", response_model=Dict[str, Any])
async def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> Dict[str, Any]:
    cookies = request.cookies
    session_id = cookies.get("session_id")
    if session_id:
        svc = AuthService(db)
        svc.revoke_current(session_id=session_id)
    # 清理 Cookie
    response.delete_cookie("refresh_token")
    response.delete_cookie("session_id")
    logger.info("auth.logout", extra={"event": "logout"})
    return {"ok": True}


@router.get("/me", response_model=UserInfo)
async def me(user=Depends(get_current_user)) -> UserInfo:
    role_names = [r.name for r in user.roles]
    return UserInfo(id=user.id, phone=user.phone, display_name=user.display_name, roles=role_names)


