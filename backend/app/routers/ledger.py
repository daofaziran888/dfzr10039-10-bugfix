"""站点来源清册接口：提交、补证、领取、落库、回查与来源清册。

这些是跨模块动作，固定路径必须排在 `/{module}` 资源路由之前注册，
否则会被动态路径当成模块名抢占。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import (
    LedgerClaimPayload,
    LedgerProofPayload,
    LedgerStorePayload,
    LedgerSubmitPayload,
)
from app.services import source_ledger

router = APIRouter(prefix="/api/ledger", tags=["站点来源清册"])


@router.get("/inventory")
def inventory(ledger_id: str | None = Query(default=None)) -> dict[str, Any]:
    """站点来源清册：按站点列出条目、批次与实际落库内容。"""
    if ledger_id and source_ledger._ledger_row(ledger_id) is None:
        raise HTTPException(status_code=404, detail=f"清册 {ledger_id} 不存在")
    return {"ok": True, "items": source_ledger.site_source_inventory(ledger_id)}


@router.get("/{ledger_id}/trace")
def trace(ledger_id: str) -> dict[str, Any]:
    """完整回查：原提交、补交记录、条目状态与批次实际内容。"""
    result = source_ledger.trace_ledger(ledger_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("message", "清册不存在"))
    return result


@router.post("/submit")
def submit(payload: LedgerSubmitPayload) -> dict[str, Any]:
    """提交清册；站点混排时每条地址按自身站点归属判定。"""
    result = source_ledger.submit_ledger(
        payload.name,
        [entry.model_dump() for entry in payload.entries],
        operator=payload.operator,
        ledger_id=payload.ledger_id,
        origin=payload.origin,
        note=payload.note,
        site_id=payload.site_id,
        urls=payload.urls,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message", "提交失败"))
    return result


@router.post("/single")
def submit_single(payload: LedgerSubmitPayload) -> dict[str, Any]:
    """单独提交一个地址到指定站点，用于验证站点授权边界。"""
    if not payload.site_id:
        raise HTTPException(status_code=400, detail="单独提交必须指定站点编号")
    urls = payload.urls if isinstance(payload.urls, list) else (
        [payload.urls] if payload.urls else [entry.url for entry in payload.entries])
    if not urls:
        raise HTTPException(status_code=400, detail="缺少来源 URL")
    result = source_ledger.submit_single_address(
        payload.name, payload.site_id, urls[0],
        operator=payload.operator, ledger_id=payload.ledger_id,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message", "提交失败"))
    return result


@router.post("/proof")
def proof(payload: LedgerProofPayload) -> dict[str, Any]:
    """补交站点核验证明；已采地址不会被重跑。"""
    result = source_ledger.attach_verification_proof(
        payload.ledger_id, payload.site_id, payload.proof,
        operator=payload.operator, urls=payload.urls, site_domain=payload.site_domain,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message", "补证失败"))
    return result


@router.post("/{ledger_id}/claim")
def claim(ledger_id: str, payload: LedgerClaimPayload | None = None) -> dict[str, Any]:
    """调度领取：只消费可领取地址，按站点生成批次与作业。"""
    try:
        return source_ledger.claim_ledger(
            ledger_id, (payload or LedgerClaimPayload()).operator)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{ledger_id}/store")
def store_batch(ledger_id: str, payload: LedgerStorePayload | None = None) -> dict[str, Any]:
    """把已领取批次标记为落库，不重复执行已采地址。"""
    result = source_ledger.store_ledger(
        ledger_id, (payload or LedgerStorePayload()).batch_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("message", "落库失败"))
    return result
