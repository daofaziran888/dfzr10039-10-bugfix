"""通用接口契约；模块字段由领域服务持有，不再为每个模块复制一份模型类。"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageResult(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    size: int = 20


class ActionResult(BaseModel):
    ok: bool
    message: str
    entry: dict[str, Any] | None = None


class EntryPayload(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    remark: str | None = None


class ActionPayload(BaseModel):
    action: str
    remark: str | None = None


# ---------------------------------------------------------------------------
# 站点来源清册
# ---------------------------------------------------------------------------

class LedgerEntrySpec(BaseModel):
    url: str = ""
    site_id: str = ""
    site_domain: str = ""
    note: str = ""


class LedgerSubmitPayload(BaseModel):
    ledger_id: str | None = None
    name: str = "站点来源清册"
    operator: str = "值班员"
    origin: str = "原始提交"
    note: str = ""
    site_id: str = ""
    urls: list[str] | str | None = None
    entries: list[LedgerEntrySpec] = Field(default_factory=list)


class LedgerProofPayload(BaseModel):
    ledger_id: str
    site_id: str = ""
    site_domain: str = ""
    proof: str
    operator: str = "核验员"
    urls: list[str] | str | None = None


class LedgerClaimPayload(BaseModel):
    operator: str = "调度员"


class LedgerStorePayload(BaseModel):
    batch_id: str | None = None
