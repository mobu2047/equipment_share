from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.core.db import get_db
from backend.core.deps import get_current_user, require_roles
from backend.core.logger import logger
from backend.models.schemas import (
    LeaseCreateRequest,
    LeaseListResponse,
    LeaseItem,
    LeaseConfirmRequest,
    LeaseActionRequest,
)
from backend.services.lease_service import LeaseService
from backend.models.lease import LeaseOrder


router = APIRouter(prefix="/api/v1/lease", tags=["lease"])


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="时间格式应为 ISO8601")


@router.post("/requests", response_model=LeaseItem, dependencies=[Depends(require_roles("TENANT", "ADMIN", "PROVIDER"))])
async def create_request(body: LeaseCreateRequest, user=Depends(get_current_user), db: Session = Depends(get_db)) -> LeaseItem:
    svc = LeaseService(db)
    lo = svc.create_request(
        requester_user_id=user.id,
        device_id=body.device_id,
        device_name=body.device_name,
        requested_start_at=_parse_dt(body.requested_start_at),
        requested_end_at=_parse_dt(body.requested_end_at),
        remark=body.remark,
    )
    return LeaseItem(
        id=lo.id,
        status=lo.status,
        device_id=lo.device_id,
        device_name_snapshot=lo.device_name_snapshot,
        requested_start_at=lo.requested_start_at.isoformat() if lo.requested_start_at else None,
        requested_end_at=lo.requested_end_at.isoformat() if lo.requested_end_at else None,
        confirmed_start_at=lo.confirmed_start_at.isoformat() if lo.confirmed_start_at else None,
        confirmed_end_at=lo.confirmed_end_at.isoformat() if lo.confirmed_end_at else None,
        created_at=lo.created_at.isoformat(),
    )


@router.get("/requests", response_model=LeaseListResponse)
async def list_requests(status: str | None = None, page: int = 1, size: int = 20, user=Depends(get_current_user), db: Session = Depends(get_db)) -> LeaseListResponse:
    svc = LeaseService(db)
    # 角色优先级：ADMIN > PROVIDER > TENANT
    role_names = {r.name for r in user.roles}
    role = "ADMIN" if "ADMIN" in role_names else ("PROVIDER" if "PROVIDER" in role_names else "TENANT")
    total, items = svc.list_for_user(user_id=user.id, role=role, status_filter=status, page=page, size=size)
    return LeaseListResponse(
        total=total,
        page=page,
        size=size,
        items=[
            LeaseItem(
                id=it.id,
                status=it.status,
                device_id=it.device_id,
                device_name_snapshot=it.device_name_snapshot,
                requested_start_at=it.requested_start_at.isoformat() if it.requested_start_at else None,
                requested_end_at=it.requested_end_at.isoformat() if it.requested_end_at else None,
                confirmed_start_at=it.confirmed_start_at.isoformat() if it.confirmed_start_at else None,
                confirmed_end_at=it.confirmed_end_at.isoformat() if it.confirmed_end_at else None,
                created_at=it.created_at.isoformat(),
            )
            for it in items
        ],
    )


@router.get("/requests/{lease_id}", response_model=LeaseItem)
async def get_request(lease_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)) -> LeaseItem:
    svc = LeaseService(db)
    lo = svc.get(lease_id)
    role_names = {r.name for r in user.roles}
    if "ADMIN" not in role_names and user.id not in {lo.requester_user_id, (lo.assigned_provider_user_id or -1)}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权查看")
    return LeaseItem(
        id=lo.id,
        status=lo.status,
        device_id=lo.device_id,
        device_name_snapshot=lo.device_name_snapshot,
        requested_start_at=lo.requested_start_at.isoformat() if lo.requested_start_at else None,
        requested_end_at=lo.requested_end_at.isoformat() if lo.requested_end_at else None,
        confirmed_start_at=lo.confirmed_start_at.isoformat() if lo.confirmed_start_at else None,
        confirmed_end_at=lo.confirmed_end_at.isoformat() if lo.confirmed_end_at else None,
        created_at=lo.created_at.isoformat(),
    )


@router.post("/requests/{lease_id}/confirm", response_model=LeaseItem, dependencies=[Depends(require_roles("ADMIN", "PROVIDER"))])
async def confirm_request(lease_id: int, body: LeaseConfirmRequest, user=Depends(get_current_user), db: Session = Depends(get_db)) -> LeaseItem:
    svc = LeaseService(db)
    lo = svc.confirm(
        lease_id=lease_id,
        device_id=body.device_id,
        confirmed_start_at=_parse_dt(body.confirmed_start_at) or datetime.utcnow(),
        confirmed_end_at=_parse_dt(body.confirmed_end_at) or datetime.utcnow(),
        actor_user_id=user.id,
        provider_user_id=body.provider_user_id,
    )
    return LeaseItem(
        id=lo.id,
        status=lo.status,
        device_id=lo.device_id,
        device_name_snapshot=lo.device_name_snapshot,
        requested_start_at=lo.requested_start_at.isoformat() if lo.requested_start_at else None,
        requested_end_at=lo.requested_end_at.isoformat() if lo.requested_end_at else None,
        confirmed_start_at=lo.confirmed_start_at.isoformat() if lo.confirmed_start_at else None,
        confirmed_end_at=lo.confirmed_end_at.isoformat() if lo.confirmed_end_at else None,
        created_at=lo.created_at.isoformat(),
    )


@router.post("/requests/{lease_id}/reject", response_model=LeaseItem, dependencies=[Depends(require_roles("ADMIN", "PROVIDER"))])
async def reject_request(lease_id: int, body: LeaseActionRequest, user=Depends(get_current_user), db: Session = Depends(get_db)) -> LeaseItem:
    svc = LeaseService(db)
    lo = svc.reject(lease_id=lease_id, reason=body.reason, actor_user_id=user.id)
    return LeaseItem(
        id=lo.id,
        status=lo.status,
        device_id=lo.device_id,
        device_name_snapshot=lo.device_name_snapshot,
        requested_start_at=lo.requested_start_at.isoformat() if lo.requested_start_at else None,
        requested_end_at=lo.requested_end_at.isoformat() if lo.requested_end_at else None,
        confirmed_start_at=lo.confirmed_start_at.isoformat() if lo.confirmed_start_at else None,
        confirmed_end_at=lo.confirmed_end_at.isoformat() if lo.confirmed_end_at else None,
        created_at=lo.created_at.isoformat(),
    )


@router.post("/requests/{lease_id}/cancel", response_model=LeaseItem, dependencies=[Depends(require_roles("TENANT", "ADMIN"))])
async def cancel_request(lease_id: int, body: LeaseActionRequest, user=Depends(get_current_user), db: Session = Depends(get_db)) -> LeaseItem:
    svc = LeaseService(db)
    lo = svc.cancel(lease_id=lease_id, reason=body.reason, actor_user_id=user.id)
    return LeaseItem(
        id=lo.id,
        status=lo.status,
        device_id=lo.device_id,
        device_name_snapshot=lo.device_name_snapshot,
        requested_start_at=lo.requested_start_at.isoformat() if lo.requested_start_at else None,
        requested_end_at=lo.requested_end_at.isoformat() if lo.requested_end_at else None,
        confirmed_start_at=lo.confirmed_start_at.isoformat() if lo.confirmed_start_at else None,
        confirmed_end_at=lo.confirmed_end_at.isoformat() if lo.confirmed_end_at else None,
        created_at=lo.created_at.isoformat(),
    )


@router.post("/requests/{lease_id}/archive", response_model=LeaseItem, dependencies=[Depends(require_roles("ADMIN"))])
async def archive_request(lease_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)) -> LeaseItem:
    svc = LeaseService(db)
    lo = svc.archive(lease_id=lease_id, actor_user_id=user.id)
    return LeaseItem(
        id=lo.id,
        status=lo.status,
        device_id=lo.device_id,
        device_name_snapshot=lo.device_name_snapshot,
        requested_start_at=lo.requested_start_at.isoformat() if lo.requested_start_at else None,
        requested_end_at=lo.requested_end_at.isoformat() if lo.requested_end_at else None,
        confirmed_start_at=lo.confirmed_start_at.isoformat() if lo.confirmed_start_at else None,
        confirmed_end_at=lo.confirmed_end_at.isoformat() if lo.confirmed_end_at else None,
        created_at=lo.created_at.isoformat(),
    )


@router.delete("/requests/{lease_id}", response_model=Dict[str, Any], dependencies=[Depends(require_roles("ADMIN"))])
async def delete_request(lease_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    # 软删除：直接标记为 cancelled（若非 archived），或从库中删除？这里用“标记为 cancelled”实现轻量删除
    svc = LeaseService(db)
    lo = svc.get(lease_id)
    if lo.status in ("pending", "rejected"):
        lo.status = "cancelled"
        db.commit()
        return {"deleted": True, "lease_id": lease_id}
    # 对于 confirmed/archived，维持审计不做物理删除
    return {"deleted": False, "reason": "该状态不允许删除"}


