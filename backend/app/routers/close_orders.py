from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.services import close_service as svc

router = APIRouter(tags=["close-orders"])


class SubmitIn(BaseModel):
    days: list[str]
    note: str | None = None


@router.post("/close-orders")
def submit(body: SubmitIn):
    """结案工单：一次勾选多日提交。整单原子——任一日失败则整单回滚。"""
    try:
        return svc.submit_order(body.days, body.note)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/close-orders/candidates")
def candidates():
    return svc.list_candidates()


@router.get("/close-orders/failures")
def failures():
    return svc.list_failures()


@router.get("/close-orders")
def list_orders():
    return svc.list_orders()


@router.get("/close-orders/{order_id}")
def order_detail(order_id: int):
    out = svc.get_order(order_id)
    if out is None:
        raise HTTPException(404, "结案工单不存在")
    return out


@router.post("/close-orders/{order_id}/retry")
def retry(order_id: int):
    try:
        return svc.retry_order(order_id)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/audit")
def audit(dock_day: str | None = None, order_id: int | None = None, limit: int = 200):
    return svc.list_audit(dock_day=dock_day, order_id=order_id, limit=limit)
