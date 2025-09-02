from __future__ import annotations

"""
租赁服务：创建/查询/状态流转/审计
"""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from backend.core.logger import logger
from backend.models.lease import LeaseOrder, LeaseOrderLog


class LeaseService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create_request(
        self,
        *,
        requester_user_id: int,
        device_id: Optional[str],
        device_name: Optional[str],
        requested_start_at: Optional[datetime],
        requested_end_at: Optional[datetime],
        remark: Optional[str],
    ) -> LeaseOrder:
        lo = LeaseOrder(
            requester_user_id=requester_user_id,
            device_id=device_id,
            device_name_snapshot=device_name,
            requested_start_at=requested_start_at,
            requested_end_at=requested_end_at,
            status="pending",
            remark=remark,
        )
        self.db.add(lo)
        self.db.flush()
        self._log(lo.id, "created", {"device_id": device_id, "device_name": device_name}, actor=requester_user_id)
        logger.info("lease.create", extra={"event": "lease_request_created", "lease_id": lo.id, "requester": requester_user_id})
        # 持久化提交，便于后续查询立即可见
        self.db.commit()
        return lo

    def _log(self, lease_id: int, event: str, data: Dict[str, Any], actor: Optional[int]) -> None:
        self.db.add(LeaseOrderLog(lease_order_id=lease_id, event_type=event, data_json=json.dumps(data, ensure_ascii=False), actor_user_id=actor))

    def get(self, lease_id: int) -> LeaseOrder:
        lo = self.db.get(LeaseOrder, lease_id)
        if not lo:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="租赁单不存在")
        return lo

    def list_for_user(self, *, user_id: int, role: str, status_filter: Optional[str], page: int, size: int) -> Tuple[int, List[LeaseOrder]]:
        q = self.db.query(LeaseOrder)
        if role == "TENANT":
            q = q.filter(LeaseOrder.requester_user_id == user_id)
        elif role == "PROVIDER":
            q = q.filter(LeaseOrder.assigned_provider_user_id == user_id)
        # ADMIN 查看全量
        if status_filter:
            q = q.filter(LeaseOrder.status == status_filter)
        total = q.count()
        items = q.order_by(LeaseOrder.created_at.desc()).offset((page - 1) * size).limit(size).all()
        return total, items

    def confirm(
        self,
        *,
        lease_id: int,
        device_id: str,
        confirmed_start_at: datetime,
        confirmed_end_at: datetime,
        actor_user_id: int,
        provider_user_id: Optional[int],
    ) -> LeaseOrder:
        lo = self.get(lease_id)
        if lo.status != "pending":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="仅 pending 可确认")
        lo.status = "confirmed"
        lo.device_id = device_id
        lo.confirmed_start_at = confirmed_start_at
        lo.confirmed_end_at = confirmed_end_at
        lo.confirmed_by_user_id = actor_user_id
        if provider_user_id:
            lo.assigned_provider_user_id = provider_user_id
        self._log(lease_id, "confirmed", {"device_id": device_id}, actor_user_id)
        logger.info("lease.confirm", extra={"event": "lease_confirmed", "lease_id": lease_id, "actor": actor_user_id})
        self.db.commit()
        return lo

    def reject(self, *, lease_id: int, reason: Optional[str], actor_user_id: int) -> LeaseOrder:
        lo = self.get(lease_id)
        if lo.status != "pending":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="仅 pending 可拒绝")
        lo.status = "rejected"
        self._log(lease_id, "rejected", {"reason": reason or ""}, actor_user_id)
        logger.info("lease.reject", extra={"event": "lease_rejected", "lease_id": lease_id, "actor": actor_user_id})
        self.db.commit()
        return lo

    def cancel(self, *, lease_id: int, reason: Optional[str], actor_user_id: int) -> LeaseOrder:
        lo = self.get(lease_id)
        if lo.status != "pending":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="仅 pending 可取消")
        if lo.requester_user_id != actor_user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅创建者可取消")
        lo.status = "cancelled"
        self._log(lease_id, "cancelled", {"reason": reason or ""}, actor_user_id)
        logger.info("lease.cancel", extra={"event": "lease_cancelled", "lease_id": lease_id, "actor": actor_user_id})
        self.db.commit()
        return lo

    def archive(self, *, lease_id: int, actor_user_id: int) -> LeaseOrder:
        lo = self.get(lease_id)
        if lo.status != "confirmed":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="仅 confirmed 可归档")
        lo.status = "archived"
        self._log(lease_id, "archived", {}, actor_user_id)
        logger.info("lease.archive", extra={"event": "lease_archived", "lease_id": lease_id, "actor": actor_user_id})
        self.db.commit()
        return lo


