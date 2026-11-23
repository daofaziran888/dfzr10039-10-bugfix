"""站点来源清册：入口网址、站点归属、核验依据与实际批次的一本账。

采集链路里的其它模块只从这份清册取数：

* 提交时逐条校验「网址域名 ↔ 站点归属」，不匹配的地址直接拦截；
* 站点未核验时条目只能是「待核」，只暴露待核说明，不能借用别的站点授权；
* 领取只消费「可领取」的条目，并按站点各自生成批次/作业；
* 补交证明只把该站点名下的待核条目转为可领取，已领取、已入库的地址不会重跑；
* 实际落库的每条记录都保留来源 URL、站点编号和核验依据。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from app.models import (
    CleanRule,
    CleanType,
    CleanedContent,
    ExtractRule,
    LedgerClaim,
    LedgerEntry,
    LedgerEntryStatus,
    SiteConfig,
    SiteType,
    StorageBatch,
    domain_matches,
    normalize_host,
    split_entry_urls,
)
from app.services.base import BaseModuleService
from app.store import store

# 已生成的批次内容留在内存里，供 trace / inventory 回查实际落库的条目。
_BATCH_CONTENTS: dict[str, StorageBatch] = {}

DEFAULT_EXTRACT_FIELDS = {
    "title": "//h1",
    "body": "//article/div[@class='content']",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_id(module: str, field: str, prefix: str, width: int = 4) -> str:
    rows = store.rows(module)
    numbers = []
    for row in rows:
        match = re.search(r"(\d+)$", str(row.get(field, "")))
        if match:
            numbers.append(int(match.group(1)))
    return f"{prefix}-{max(numbers, default=0) + 1:0{width}d}"


def _safe_token(value: str) -> str:
    # 保留连字符，编号形如 BATCH-LEDG-0001-SITE-A 便于人工追溯。
    token = re.sub(r"[^0-9A-Za-z-]+", "_", str(value or "")).strip("_")
    return token or "SITE"


# ---------------------------------------------------------------------------
# 站点读取
# ---------------------------------------------------------------------------

def all_sites() -> list[SiteConfig]:
    return [SiteConfig.from_seed_row(row) for row in store.rows("crawl_site")]


def site_by_id(site_id: str) -> SiteConfig | None:
    target = str(site_id or "").strip()
    if not target:
        return None
    for row in store.rows("crawl_site"):
        if str(row.get("站点编号", "")) == target:
            return SiteConfig.from_seed_row(row)
    return None


def site_by_domain(domain: str) -> SiteConfig | None:
    target = str(domain or "").strip().lower()
    for row in store.rows("crawl_site"):
        if str(row.get("站点域名", "")).strip().lower() == target:
            return SiteConfig.from_seed_row(row)
    return None


def resolve_site_for_host(host: str, sites: list[SiteConfig] | None = None) -> SiteConfig | None:
    """按主机名匹配登记站点，取域名最具体（最长）的那个。"""
    candidates = [s for s in (sites if sites is not None else all_sites()) if domain_matches(host, s.domain)]
    if not candidates:
        return None
    return sorted(candidates, key=lambda s: len(s.domain), reverse=True)[0]


def _site_row(site_id: str) -> dict[str, Any] | None:
    for row in store.rows("crawl_site"):
        if str(row.get("站点编号", "")) == str(site_id or ""):
            return row
    return None


def _site_is_verified(site: SiteConfig) -> bool:
    return bool(site.verified)


def _site_reason(site: SiteConfig) -> str:
    if not site.verified:
        return f"站点 {site.site_id}（{site.domain}）域名归属待核验，仅可查看待核说明，不得借用其他站点授权入库"
    if site.robots_policy.lower() == "disallow":
        return f"站点 {site.site_id}（{site.domain}）Robots 协议禁止采集"
    return ""


# ---------------------------------------------------------------------------
# 清册与提交
# ---------------------------------------------------------------------------

def create_ledger(name: str, operator: str = "值班员", ledger_id: str | None = None) -> dict[str, Any]:
    rows = store.rows("source_ledger")
    if ledger_id:
        for row in rows:
            if str(row.get("清册编号")) == ledger_id:
                return row
    ledger_id = ledger_id or _next_id("source_ledger", "清册编号", "LEDG")
    entry = {
        "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
        "status": "待核验",
        "pending": True,
        "abnormal": False,
        "清册编号": ledger_id,
        "清册名称": str(name or ledger_id),
        "站点范围": "",
        "条目总数": 0,
        "待核数": 0,
        "可领取数": 0,
        "已领取数": 0,
        "已入库数": 0,
        "越权拦截数": 0,
        "批次范围": "",
        "创建人": operator,
        "创建时间": _now(),
    }
    rows.append(entry)
    return entry


def _ledger_row(ledger_id: str) -> dict[str, Any] | None:
    for row in store.rows("source_ledger"):
        if str(row.get("清册编号", "")) == str(ledger_id):
            return row
    return None


def _ledger_entries(ledger_id: str) -> list[dict[str, Any]]:
    return [row for row in store.rows("ledger_entry")
            if str(row.get("清册编号", "")) == str(ledger_id)]


def _entry_from_row(row: dict[str, Any]) -> LedgerEntry:
    try:
        status = LedgerEntryStatus(str(row.get("条目状态", LedgerEntryStatus.PENDING.value)))
    except ValueError:
        status = LedgerEntryStatus.PENDING
    return LedgerEntry(
        entry_id=str(row.get("条目编号", "")),
        ledger_id=str(row.get("清册编号", "")),
        url=str(row.get("来源URL", "")),
        host=str(row.get("地址主机", "")),
        site_id=str(row.get("站点编号", "")),
        site_domain=str(row.get("站点域名", "")),
        status=status,
        origin=str(row.get("提交来源", "原始提交")),
        submitted_at=str(row.get("提交时间", "")),
        verified_at=str(row.get("核验时间", "")) or None,
        proof=str(row.get("核验依据", "")),
        batch_id=str(row.get("批次编号", "")),
        job_id=str(row.get("作业编号", "")),
        note=str(row.get("处理说明", "")),
        claimed_at=str(row.get("领取时间", "")) or None,
        stored_at=str(row.get("入库时间", "")) or None,
        source_site_id=str(row.get("实际来源站点", "")),
    )


def _normalize_entry_spec(spec: dict[str, Any] | str) -> dict[str, str]:
    if isinstance(spec, str):
        return {"url": spec, "site_id": ""}
    url = str(
        spec.get("url")
        or spec.get("URL")
        or spec.get("来源URL")
        or spec.get("入口URL")
        or ""
    )
    site_id = str(
        spec.get("site_id")
        or spec.get("站点编号")
        or spec.get("目标站点编号")
        or ""
    )
    site_domain = str(spec.get("site_domain") or spec.get("站点域名") or "")
    note = str(spec.get("note") or spec.get("说明") or "")
    return {"url": url, "site_id": site_id, "site_domain": site_domain, "note": note}


def submit_entries(
    ledger_id: str,
    site_id: str = "",
    urls: list[str] | str | None = None,
    *,
    operator: str = "值班员",
    origin: str = "原始提交",
    note: str = "",
    site_domain: str = "",
) -> dict[str, Any]:
    """向一份清册提交地址；逐条判定域名归属与站点资格。

    返回本次提交的回执，包含新增、待核、可领取、拦截和重复的明细。
    """
    ledger = _ledger_row(ledger_id)
    if ledger is None:
        return {"ok": False, "message": f"清册 {ledger_id} 不存在", "清册编号": ledger_id}

    if isinstance(urls, str):
        raw_urls = split_entry_urls(urls)
    else:
        raw_urls = [str(u).strip() for u in (urls or []) if str(u).strip()]

    target_site = site_by_id(site_id) if site_id else (site_by_domain(site_domain) if site_domain else None)
    unknown_site_reason = ""
    if site_id and target_site is None:
        # 站点未登记时仍逐条记一笔拦截，避免整份混合提交被一条坏数据打断。
        unknown_site_reason = f"站点 {site_id} 未登记，不能提交"
    elif site_domain and target_site is None:
        unknown_site_reason = f"站点域名 {site_domain} 未登记，不能提交"

    existing_urls = {str(row.get("来源URL", "")).strip() for row in _ledger_entries(ledger_id)}
    submission_id = _next_id("ledger_submission", "提交编号", "SUBM")
    created: list[dict[str, Any]] = []
    duplicates: list[str] = []
    blocked: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    claimable: list[dict[str, Any]] = []
    sites_seen: set[str] = set()

    entry_seq = len(_ledger_entries(ledger_id))

    for url in raw_urls:
        if not url:
            continue
        if url in existing_urls:
            duplicates.append(url)
            continue
        existing_urls.add(url)
        host = normalize_host(url)
        chosen = target_site

        if not chosen:
            chosen = resolve_site_for_host(host)

        reason = ""
        status = LedgerEntryStatus.PENDING
        actual_site = chosen

        if unknown_site_reason:
            status = LedgerEntryStatus.BLOCKED
            reason = unknown_site_reason
        elif not host:
            status = LedgerEntryStatus.BLOCKED
            reason = f"无法解析网址域名：{url}"
        elif chosen is None:
            status = LedgerEntryStatus.BLOCKED
            reason = f"网址 {host} 未匹配到任何已登记站点"
        elif not domain_matches(host, chosen.domain):
            # 拦截条目记在「被声明的站点」名下，便于追溯是谁提交的越权地址；
            # 真正归属的站点写进说明，不因这条提交平白多出待办。
            status = LedgerEntryStatus.BLOCKED
            owner = resolve_site_for_host(host)
            if owner is not None:
                reason = (f"越权地址：网址域名 {host} 归属站点 {owner.site_id}（{owner.domain}），"
                          f"不能记入 {chosen.site_id}（{chosen.domain}）的授权")
            else:
                reason = f"越权地址：网址域名 {host} 不属于站点 {chosen.site_id}（{chosen.domain}）"
        elif not _site_is_verified(chosen):
            status = LedgerEntryStatus.PENDING
            reason = _site_reason(chosen)
        elif chosen.robots_policy.lower() == "disallow":
            status = LedgerEntryStatus.BLOCKED
            reason = _site_reason(chosen)
        else:
            status = LedgerEntryStatus.CLAIMABLE

        entry_seq += 1
        entry_id = f"ENT-{_safe_token(ledger_id)}-{entry_seq:04d}"
        row = {
            "id": max((int(r.get("id", 0)) for r in store.rows("ledger_entry")), default=0) + 1,
            "status": status.value,
            "pending": status in (LedgerEntryStatus.PENDING, LedgerEntryStatus.CLAIMABLE),
            "abnormal": status == LedgerEntryStatus.BLOCKED,
            "条目编号": entry_id,
            "清册编号": ledger_id,
            "来源URL": url,
            "地址主机": host,
            "站点编号": actual_site.site_id if actual_site else (
                chosen.site_id if chosen else (site_id or "")),
            "站点域名": actual_site.domain if actual_site else (
                chosen.domain if chosen else site_domain),
            "条目状态": status.value,
            "提交来源": origin,
            "提交时间": _now(),
            "核验时间": "" if status == LedgerEntryStatus.BLOCKED else (
                actual_site.verified_at if actual_site else ""),
            "核验依据": "" if status == LedgerEntryStatus.BLOCKED else (
                actual_site.verification_proof if actual_site else ""),
            "批次编号": "",
            "作业编号": "",
            "处理说明": reason or note,
            "领取时间": "",
            "入库时间": "",
            "实际来源站点": actual_site.site_id if actual_site else "",
        }
        store.rows("ledger_entry").append(row)
        created.append(row)
        if status == LedgerEntryStatus.BLOCKED:
            blocked.append({"来源URL": url, "站点编号": row["站点编号"], "说明": reason})
        elif status == LedgerEntryStatus.PENDING:
            pending.append({"来源URL": url, "站点编号": row["站点编号"], "说明": reason})
        else:
            claimable.append({"来源URL": url, "站点编号": row["站点编号"]})
        if row["站点编号"]:
            sites_seen.add(str(row["站点编号"]))

    submission = {
        "id": max((int(r.get("id", 0)) for r in store.rows("ledger_submission")), default=0) + 1,
        "status": "已记录",
        "pending": bool(pending),
        "abnormal": bool(blocked),
        "提交编号": submission_id,
        "清册编号": ledger_id,
        "提交类型": origin,
        "站点编号": site_id or (target_site.site_id if target_site else ""),
        "来源URL": "，".join(raw_urls),
        "条目编号": "，".join(str(row["条目编号"]) for row in created),
        "新增条目数": len(created),
        "可领取数": len(claimable),
        "待核数": len(pending),
        "拦截数": len(blocked),
        "重复地址": "，".join(duplicates),
        "提交人": operator,
        "提交时间": _now(),
        "说明": note,
    }
    store.rows("ledger_submission").append(submission)

    # 核验结果 / 作业清单同步：每个涉及站点维护一条来自本清册的采集作业。
    for sid in sorted(sites_seen):
        site = site_by_id(sid)
        if site is not None:
            _sync_site_job(ledger_id, site)

    _refresh_ledger(ledger_id)
    return {
        "ok": True,
        "message": (
            f"已提交 {len(created)} 条：可领取 {len(claimable)}，待核 {len(pending)}，"
            f"拦截 {len(blocked)}" + (f"，重复跳过 {len(duplicates)}" if duplicates else "")
        ),
        "清册编号": ledger_id,
        "提交编号": submission_id,
        "提交类型": origin,
        "新增": [row["条目编号"] for row in created],
        "可领取": claimable,
        "待核": pending,
        "拦截": blocked,
        "重复": duplicates,
        "清册": dict(ledger),
    }


def submit_ledger(
    name: str,
    entries: list[dict[str, Any] | str] | None = None,
    *,
    operator: str = "值班员",
    ledger_id: str | None = None,
    origin: str = "原始提交",
    note: str = "",
    site_id: str = "",
    urls: list[str] | str | None = None,
) -> dict[str, Any]:
    """创建（或续用）一份清册并提交地址。entries 支持 {url, site_id} 列表。"""
    ledger = create_ledger(name, operator, ledger_id)
    ledger_id = str(ledger["清册编号"])
    results = []

    if urls is not None:
        results.append(submit_entries(ledger_id, site_id, urls, operator=operator,
                                      origin=origin, note=note))
    for spec in entries or []:
        normalized = _normalize_entry_spec(spec)
        if not normalized["url"]:
            continue
        results.append(submit_entries(
            ledger_id,
            normalized.get("site_id", ""),
            [normalized["url"]],
            operator=operator,
            origin=origin,
            note=normalized.get("note", "") or note,
            site_domain=normalized.get("site_domain", ""),
        ))

    merged = {
        "ok": all(item.get("ok") for item in results) if results else True,
        "清册编号": ledger_id,
        "提交": results,
        "清册": _refresh_ledger(ledger_id),
    }
    merged["message"] = "；".join(item.get("message", "") for item in results) or "清册已创建"
    return merged


def submit_mixed_ledger(
    name: str,
    entries: list[dict[str, Any] | str],
    *,
    operator: str = "值班员",
    ledger_id: str | None = None,
    origin: str = "原始提交",
) -> dict[str, Any]:
    """混合来源提交：每条地址按自身站点归属判定，不继承其它站点的授权。"""
    return submit_ledger(name, entries, operator=operator, ledger_id=ledger_id, origin=origin)


def submit_single_address(
    name: str,
    site_id: str,
    url: str,
    *,
    operator: str = "值班员",
    ledger_id: str | None = None,
) -> dict[str, Any]:
    """单独提交一个地址：用于验证「A 的授权不能收 B 的网址」。"""
    return submit_ledger(name, None, operator=operator, ledger_id=ledger_id,
                         site_id=site_id, urls=[url])


# ---------------------------------------------------------------------------
# 核验证明
# ---------------------------------------------------------------------------

def attach_verification_proof(
    ledger_id: str,
    site_id: str,
    proof: str,
    *,
    operator: str = "核验员",
    urls: list[str] | str | None = None,
    site_domain: str = "",
) -> dict[str, Any]:
    """补交站点核验证明：只升级该站点的待核条目，不重跑已领取/已入库地址。"""
    ledger = _ledger_row(ledger_id)
    if ledger is None:
        return {"ok": False, "message": f"清册 {ledger_id} 不存在", "清册编号": ledger_id}

    site = site_by_id(site_id) if site_id else (site_by_domain(site_domain) if site_domain else None)
    if site is None:
        return {"ok": False, "message": f"站点 {site_id or site_domain} 未登记", "清册编号": ledger_id}
    proof = str(proof or "").strip()
    if not proof:
        return {"ok": False, "message": "补交证明不能为空", "清册编号": ledger_id}

    requested_urls = set(split_entry_urls(urls) if isinstance(urls, str) else
                         [str(u).strip() for u in (urls or []) if str(u).strip()])

    # 历史已采地址不允许在本清册里重跑。
    entries = _ledger_entries(ledger_id)
    already_done = {
        str(row.get("来源URL", ""))
        for row in entries
        if str(row.get("条目状态")) in (LedgerEntryStatus.CLAIMED.value, LedgerEntryStatus.STORED.value)
    }
    rerun = sorted(requested_urls & already_done)

    verification_id = _next_id("site_verification", "核验编号", "VERI")
    store.rows("site_verification").append({
        "id": max((int(r.get("id", 0)) for r in store.rows("site_verification")), default=0) + 1,
        "status": "已验证",
        "pending": False,
        "abnormal": False,
        "核验编号": verification_id,
        "清册编号": ledger_id,
        "站点编号": site.site_id,
        "站点域名": site.domain,
        "核验结果": "已验证",
        "核验依据": proof,
        "核验时间": _now(),
        "操作人": operator,
        "重跑拦截": "，".join(rerun),
    })

    # 写入站点主档，核验结果可被爬虫任务清单直接读取。
    site_row = _site_row(site.site_id)
    if site_row is not None:
        site_row["status"] = "已验证"
        site_row["pending"] = False
        site_row["核验证明"] = proof
        site_row["核验时间"] = _now()

    # 站点核验结果已落到站点主档时，读取时同步本清册的待核条目；
    # 其它清册的条目由各自的领取流程在读取时重算，避免静默改写别人的账。
    upgraded: list[str] = []
    upgraded_urls: list[str] = []
    item_details: list[dict[str, str]] = []
    blocked: list[dict[str, Any]] = []
    for row in entries:
        if str(row.get("站点编号")) != site.site_id:
            continue
        current = str(row.get("条目状态"))
        if current != LedgerEntryStatus.PENDING.value:
            continue
        if requested_urls and str(row.get("来源URL")) not in requested_urls:
            continue
        host = str(row.get("地址主机")) or normalize_host(str(row.get("来源URL", "")))
        if not domain_matches(host, site.domain):
            row["条目状态"] = LedgerEntryStatus.BLOCKED.value
            row["status"] = LedgerEntryStatus.BLOCKED.value
            row["abnormal"] = True
            row["pending"] = False
            row["处理说明"] = f"越权地址：{host} 不属于站点 {site.site_id}（{site.domain}）"
            blocked.append({"来源URL": row.get("来源URL"), "说明": row["处理说明"]})
            continue
        row["条目状态"] = LedgerEntryStatus.CLAIMABLE.value
        row["status"] = LedgerEntryStatus.CLAIMABLE.value
        row["pending"] = True
        row["核验时间"] = _now()
        row["核验依据"] = proof
        row["处理说明"] = "站点核验已通过，可被调度领取"
        upgraded.append(str(row.get("条目编号")))
        upgraded_urls.append(str(row.get("来源URL")))
        item_details.append({"条目编号": str(row.get("条目编号")), "来源URL": str(row.get("来源URL"))})

    # 作业清单同步：本清册该站点的作业核验状态更新为已验证。
    _sync_site_job(ledger_id, site_by_id(site.site_id) or site)

    # 补交也要留痕：与原始提交并列一条，保证两段记录都能回查。
    store.rows("ledger_submission").append({
        "id": max((int(r.get("id", 0)) for r in store.rows("ledger_submission")), default=0) + 1,
        "status": "已记录",
        "pending": False,
        "abnormal": bool(blocked),
        "提交编号": _next_id("ledger_submission", "提交编号", "SUBM"),
        "清册编号": ledger_id,
        "提交类型": "补交",
        "站点编号": site.site_id,
        "来源URL": "，".join(upgraded_urls),
        "条目编号": "，".join(upgraded),
        "新增条目数": 0,
        "可领取数": len(upgraded),
        "待核数": 0,
        "拦截数": len(blocked),
        "重复地址": "，".join(rerun),
        "提交人": operator,
        "提交时间": _now(),
        "说明": f"补交核验证明：{proof}",
    })

    _refresh_ledger(ledger_id)
    return {
        "ok": True,
        "message": (f"站点 {site.site_id}（{site.domain}）核验已记录，"
                    f"升级 {len(upgraded)} 条待核地址"
                    + (f"；已采地址不重跑 {len(rerun)} 条" if rerun else "")),
        "清册编号": ledger_id,
        "核验编号": verification_id,
        "站点编号": site.site_id,
        "站点域名": site.domain,
        "核验依据": proof,
        "升级条目": upgraded,
        "升级地址": upgraded_urls,
        "明细": item_details,
        "提交编号": store.rows("ledger_submission")[-1].get("提交编号", ""),
        "重跑拦截": rerun,
        "越权拦截": blocked,
        "清册": _refresh_ledger(ledger_id),
    }


# ---------------------------------------------------------------------------
# 领取 / 执行 / 入库
# ---------------------------------------------------------------------------

def _job_for(ledger_id: str, site_id: str) -> dict[str, Any] | None:
    for row in store.rows("crawl_job"):
        if (str(row.get("清册编号", "")) == ledger_id
                and str(row.get("站点编号", "")) == site_id):
            return row
    return None


def _sync_site_job(ledger_id: str, site: SiteConfig, *,
                   status: str | None = None, claimed: bool = False) -> dict[str, Any]:
    """把清册里的站点同步到采集任务清单，作业只取本清册内归属该站点的地址。"""
    job_id = f"JOB-{_safe_token(ledger_id)}-{_safe_token(site.site_id)}"
    owned_urls = [
        str(row.get("来源URL", ""))
        for row in _ledger_entries(ledger_id)
        if str(row.get("站点编号")) == site.site_id
        and str(row.get("条目状态")) != LedgerEntryStatus.BLOCKED.value
    ]
    rows = store.rows("crawl_job")
    row = _job_for(ledger_id, site.site_id)
    if row is None:
        row = {
            "id": max((int(r.get("id", 0)) for r in rows), default=0) + 1,
            "pending": True,
            "abnormal": False,
            "作业编号": job_id,
            "目标站点": site.domain,
            "入口URL": ",".join(owned_urls),
            "最大深度": "3",
            "并发线程": "4",
            "调度策略": "interval",
            "完成页数": "0",
            "失败页数": "0",
            "清册编号": ledger_id,
            "站点编号": site.site_id,
            "站点域名": site.domain,
            "核验状态": "已验证" if site.verified else "待核验",
            "核验依据": site.verification_proof,
            "status": "待执行",
        }
        rows.append(row)
    else:
        row["目标站点"] = site.domain
        row["入口URL"] = ",".join(owned_urls)
        row["站点域名"] = site.domain
        row["核验状态"] = "已验证" if site.verified else "待核验"
        row["核验依据"] = site.verification_proof
    if status:
        row["status"] = status
    row["pending"] = row.get("status") != "已完成"
    row["abnormal"] = str(row.get("核验状态")) == "待核验"
    return row


def _default_rules(site: SiteConfig) -> tuple[list[ExtractRule], list[CleanRule]]:
    extract_rows = [
        row for row in store.rows("content_extract")
        if str(row.get("目标站点", "")).strip().lower() == site.domain.lower()
        and str(row.get("status", "生效中")) == "生效中"
    ]
    if extract_rows:
        rules = [ExtractRule.from_seed_row(row) for row in extract_rows]
    else:
        rules = [ExtractRule(
            rule_id=f"CONT-{_safe_token(site.site_id)}",
            target_site=site.domain,
            xpath_selector="//article",
            field_mapping=dict(DEFAULT_EXTRACT_FIELDS),
            status="生效中",
        )]

    # 清洗规则只保留命中抽取字段的那些，避免出现「规则字段不存在 → 清空记录」。
    field_names: set[str] = set()
    for rule in rules:
        for key, value in rule.field_mapping.items():
            parts = [p.strip() for p in str(value).split(",") if p.strip()]
            if len(parts) > 1:
                field_names.update(parts)
            elif key != "raw":
                field_names.add(str(key))
            elif parts:
                field_names.add(parts[0])
    clean_rules = [
        CleanRule.from_seed_row(row) for row in store.rows("field_clean")
        if str(row.get("status")) == "生效中"
        and str(row.get("目标字段", "")) in field_names
    ]
    # 未被规则覆盖的字段补一条基础去空格，避免清洗后字段被丢掉。
    covered = {rule.target_field for rule in clean_rules}
    for field in sorted(field_names - covered):
        clean_rules.append(CleanRule(
            rule_id=f"FIEL-{_safe_token(site.site_id)}-{field}",
            target_field=field,
            clean_type=CleanType.TRIM,
            null_strategy="skip",
        ))
    return rules, clean_rules


def _batch_id_for(ledger_id: str, site: SiteConfig) -> str:
    """每轮领取生成独立批次，历史批次内容不被覆盖。"""
    prefix = f"BATCH-{_safe_token(ledger_id)}-{_safe_token(site.site_id)}"
    existing = {
        str(row.get("批次编号", "")) for row in store.rows("batch_store")
        if str(row.get("清册编号", "")) == ledger_id
        and str(row.get("站点编号", "")) == site.site_id
    }
    if prefix in existing:
        round_no = 1
        while f"{prefix}-R{round_no}" in existing:
            round_no += 1
        return f"{prefix}-R{round_no}"
    return prefix


def _build_batch(ledger_id: str, site: SiteConfig,
                 entries: list[dict[str, Any]], operator: str) -> StorageBatch:
    """按站点生成批次：只放本清单里属于该站点的条目，并保留来源与核验依据。"""
    from app.integration import _apply_clean_rules, _simulate_extract, _persist_batch

    job_id = f"JOB-{_safe_token(ledger_id)}-{_safe_token(site.site_id)}"
    batch_id = _batch_id_for(ledger_id, site)
    extract_rules, clean_rules = _default_rules(site)
    records: list[CleanedContent] = []

    for index, row in enumerate(entries):
        url = str(row.get("来源URL", ""))
        host = normalize_host(url)
        if not domain_matches(host, site.domain):
            # 双保险：即使条目被错放，也不会写进该站点的批次。
            row["条目状态"] = LedgerEntryStatus.BLOCKED.value
            row["status"] = LedgerEntryStatus.BLOCKED.value
            row["abnormal"] = True
            row["处理说明"] = f"越权地址：{host} 不属于站点 {site.site_id}（{site.domain}）"
            continue
        rule = extract_rules[index % len(extract_rules)] if extract_rules else None
        if rule is None:
            continue
        raw = _simulate_extract(url, rule)
        cleaned = _apply_clean_rules(raw, clean_rules)
        cleaned.site_id = site.site_id
        cleaned.ledger_entry_id = str(row.get("条目编号", ""))
        cleaned.source_proof = str(row.get("核验依据", "")) or site.verification_proof
        records.append(cleaned)

        row["批次编号"] = batch_id
        row["作业编号"] = job_id
        row["领取时间"] = _now()
        row["条目状态"] = LedgerEntryStatus.CLAIMED.value
        row["status"] = LedgerEntryStatus.CLAIMED.value
        row["pending"] = False
        row["实际来源站点"] = site.site_id

    batch = StorageBatch(
        batch_id=batch_id,
        source_job_id=job_id,
        row_count=len(records),
        storage_path=f"/data/batches/{_safe_token(ledger_id)}/{_safe_token(site.site_id)}/",
        compression="gzip",
        schema_version="v1",
        stored_at=_now(),
        status="已落盘",
        records=records,
        site_id=site.site_id,
        site_domain=site.domain,
        ledger_id=ledger_id,
    )
    batch.checksum = batch.compute_checksum()
    batch.status = "校验通过" if records and all(r.is_valid() for r in records) else "校验失败"

    if not records:
        # 没有任何属于本站点的有效记录时不生成批次，避免出现空批次占账。
        return batch

    rows = store.rows("batch_store")
    existing = next((r for r in rows if str(r.get("批次编号")) == batch_id), None)
    if existing is None:
        _persist_batch(batch)
        persisted = next((r for r in rows if str(r.get("批次编号")) == batch_id), None)
        if persisted is not None:
            persisted.update({
                "站点编号": site.site_id,
                "站点域名": site.domain,
                "清册编号": ledger_id,
                "数据来源": "站点来源清册",
            })
    else:
        existing.update({
            "数据行数": batch.row_count,
            "存储路径": batch.storage_path,
            "校验摘要": batch.checksum[:16],
            "入库时间": batch.stored_at,
            "站点编号": site.site_id,
            "站点域名": site.domain,
            "清册编号": ledger_id,
            "status": batch.status,
            "pending": False,
            "abnormal": batch.status == "校验失败",
        })
    _BATCH_CONTENTS[batch_id] = batch
    return batch


def _reconcile_verified_sites(ledger_id: str) -> None:
    """站点主档已核验时，把本清册中该站点的待核条目升级为可领取。

    只升级「待核」，已领取/已入库的地址保持原状态，不会因此重跑。
    """
    for row in _ledger_entries(ledger_id):
        if str(row.get("条目状态")) != LedgerEntryStatus.PENDING.value:
            continue
        site = site_by_id(str(row.get("站点编号", "")))
        if site is None or not site.verified:
            continue
        host = str(row.get("地址主机")) or normalize_host(str(row.get("来源URL", "")))
        if not domain_matches(host, site.domain):
            continue
        row["条目状态"] = LedgerEntryStatus.CLAIMABLE.value
        row["status"] = LedgerEntryStatus.CLAIMABLE.value
        row["pending"] = True
        row["核验时间"] = row.get("核验时间") or site.verified_at or _now()
        row["核验依据"] = row.get("核验依据") or site.verification_proof
        row["处理说明"] = "站点核验已通过，可被调度领取"


def claim_ledger(ledger_id: str, operator: str = "调度员") -> dict[str, Any]:
    """领取一份清册：只消费可领取的地址，按站点分别生成作业与批次。"""
    ledger = _ledger_row(ledger_id)
    if ledger is None:
        raise ValueError(f"清册 {ledger_id} 不存在")

    _reconcile_verified_sites(ledger_id)
    rows = _ledger_entries(ledger_id)
    claimable = [row for row in rows if str(row.get("条目状态")) == LedgerEntryStatus.CLAIMABLE.value]
    waiting: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []

    for row in rows:
        state = str(row.get("条目状态"))
        if state == LedgerEntryStatus.PENDING.value:
            site = site_by_id(str(row.get("站点编号"))) or None
            waiting.append({
                "条目编号": row.get("条目编号"),
                "来源URL": row.get("来源URL"),
                "站点编号": row.get("站点编号"),
                "站点域名": row.get("站点域名"),
                "待核说明": row.get("处理说明") or (site and _site_reason(site)) or "站点待核验",
            })
        elif state == LedgerEntryStatus.BLOCKED.value:
            blocked.append({
                "条目编号": row.get("条目编号"),
                "来源URL": row.get("来源URL"),
                "站点编号": row.get("站点编号"),
                "拦截说明": row.get("处理说明"),
            })

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in claimable:
        grouped.setdefault(str(row.get("站点编号", "")), []).append(row)

    claimed_entries: list[LedgerEntry] = []
    per_site: dict[str, str] = {}
    batches: list[StorageBatch] = []
    executable: list[str] = []

    for site_id, site_entries in grouped.items():
        site = site_by_id(site_id)
        if site is None:
            for row in site_entries:
                row["条目状态"] = LedgerEntryStatus.BLOCKED.value
                row["status"] = LedgerEntryStatus.BLOCKED.value
                row["处理说明"] = f"站点 {site_id} 未登记，无法领取"
                blocked.append({"条目编号": row.get("条目编号"),
                                "来源URL": row.get("来源URL"),
                                "站点编号": site_id,
                                "拦截说明": row["处理说明"]})
            continue

        # 作业/批次都从同一份清册取数。
        _sync_site_job(ledger_id, site, status="采集执行", claimed=True)
        batch = _build_batch(ledger_id, site, site_entries, operator)
        batches.append(batch)
        if batch.records:
            per_site[site_id] = batch.batch_id
            executable.append(site_id)
        # 本轮被拦截的条目（无论来自错放数据还是空批次）统一登记进来。
        for row in site_entries:
            if str(row.get("条目状态")) != LedgerEntryStatus.BLOCKED.value:
                continue
            if any(item.get("条目编号") == row.get("条目编号") for item in blocked):
                continue
            row["pending"] = False
            row["处理说明"] = row.get("处理说明") or "地址未通过站点归属校验，未入库"
            blocked.append({"条目编号": row.get("条目编号"),
                            "来源URL": row.get("来源URL"),
                            "站点编号": site_id,
                            "拦截说明": row["处理说明"]})
        for row in site_entries:
            if str(row.get("条目状态")) == LedgerEntryStatus.CLAIMED.value:
                claimed_entries.append(_entry_from_row(row))

    claim_record = {
        "id": max((int(r.get("id", 0)) for r in store.rows("ledger_claim")), default=0) + 1,
        "status": "已领取" if claimed_entries else "无可用地址",
        "pending": bool(waiting),
        "abnormal": bool(blocked),
        "领取编号": _next_id("ledger_claim", "领取编号", "CLAIM"),
        "清册编号": ledger_id,
        "领取人": operator,
        "领取时间": _now(),
        "领取条目数": len(claimed_entries),
        "等待条目数": len(waiting),
        "拦截条目数": len(blocked),
        "可执行站点": "，".join(executable),
        "批次范围": "，".join(per_site.values()),
        "等待说明": "；".join(f"{w['站点编号']}:{w['待核说明']}" for w in waiting),
    }
    store.rows("ledger_claim").append(claim_record)

    _refresh_ledger(ledger_id)

    if claimed_entries:
        message = (f"已领取 {len(claimed_entries)} 条地址，可执行站点：{('、'.join(executable)) or '无'}"
                   + (f"；{len(waiting)} 条待核地址不参与本次领取" if waiting else ""))
    elif waiting:
        message = (f"本次没有可领取地址；{len(waiting)} 条待核地址仅可查看待核说明，"
                   f"可执行站点：无")
    else:
        message = "本次没有可领取地址"

    claim = LedgerClaim(
        ledger_id=ledger_id,
        batch_id="，".join(per_site.values()),
        claimed=claimed_entries,
        blocked=blocked,
        waiting=waiting,
        per_site=per_site,
        executable_sites=executable,
        can_execute=bool(executable),
        message=message,
    )
    result = claim.to_dict()
    result.update({
        "ok": True,
        "领取编号": claim_record["领取编号"],
        "说明": message,
        "清册": _refresh_ledger(ledger_id),
        "批次": [{"批次编号": b.batch_id, "站点编号": b.site_id, "站点域名": b.site_domain,
                  "数据行数": b.row_count, "状态": b.status} for b in batches],
    })
    return result


def store_ledger(ledger_id: str, batch_id: str | None = None) -> dict[str, Any]:
    """把已领取批次标记为落库，不重新抽取、不重跑任何已采地址。"""
    ledger = _ledger_row(ledger_id)
    if ledger is None:
        return {"ok": False, "message": f"清册 {ledger_id} 不存在", "清册编号": ledger_id}

    rows = _ledger_entries(ledger_id)
    targets = [row for row in rows
               if str(row.get("条目状态")) == LedgerEntryStatus.CLAIMED.value
               and (not batch_id or str(row.get("批次编号")) == batch_id)]
    if not targets:
        return {
            "ok": True,
            "message": "没有待落库的已领取地址；同一清册已采地址不会重复执行",
            "清册编号": ledger_id,
            "落库": [],
        }

    by_batch: dict[str, list[dict[str, Any]]] = {}
    for row in targets:
        by_batch.setdefault(str(row.get("批次编号", "")), []).append(row)

    landed = []
    for bid, entries in by_batch.items():
        batch = _BATCH_CONTENTS.get(bid)
        batch_row = next((r for r in store.rows("batch_store") if str(r.get("批次编号")) == bid), None)
        status = batch.status if batch is not None else str((batch_row or {}).get("status", "校验失败"))
        if batch_row is not None:
            batch_row["status"] = status
            batch_row["pending"] = False
            batch_row["abnormal"] = status == "校验失败"
        for row in entries:
            row["条目状态"] = LedgerEntryStatus.STORED.value
            row["status"] = LedgerEntryStatus.STORED.value
            row["pending"] = False
            row["入库时间"] = _now()
        site_id = str(entries[0].get("站点编号", ""))
        _sync_site_job(ledger_id, site_by_id(site_id) or SiteConfig(
            site_id=site_id, domain=str(entries[0].get("站点域名", "")),
            site_type=SiteType.NEWS, robots_policy="allow", language="zh-CN",
            region="CN", access_method="direct", registered_at="",
        ), status="已完成" if status == "校验通过" else "部分失败")
        landed.append({
            "批次编号": bid,
            "站点编号": site_id,
            "站点域名": entries[0].get("站点域名", ""),
            "状态": status,
            "条目": [{
                "来源URL": row.get("来源URL"),
                "条目编号": row.get("条目编号"),
                "站点编号": row.get("站点编号"),
                "核验依据": row.get("核验依据"),
            } for row in entries],
        })

    _refresh_ledger(ledger_id)
    return {
        "ok": True,
        "message": f"已落库 {sum(len(v) for v in by_batch.values())} 条地址，来源与核验依据已随条目保留",
        "清册编号": ledger_id,
        "落库": landed,
        "清册": _refresh_ledger(ledger_id),
    }


# ---------------------------------------------------------------------------
# 回查
# ---------------------------------------------------------------------------

def _batch_records(batch_id: str) -> list[dict[str, Any]]:
    batch = _BATCH_CONTENTS.get(batch_id)
    if batch is not None:
        return [{
            "来源URL": record.source_url,
            "站点编号": record.site_id,
            "清册条目": record.ledger_entry_id,
            "核验依据": record.source_proof,
            "字段": dict(record.fields),
            "有效": record.is_valid(),
            "明细来源": "批次记录",
        } for record in batch.records]
    # 历史批次没有逐条内容缓存时，用清册里已入库的条目还原可追溯的落库清单。
    return [{
        "来源URL": row.get("来源URL", ""),
        "站点编号": row.get("站点编号", ""),
        "清册条目": row.get("条目编号", ""),
        "核验依据": row.get("核验依据", ""),
        "字段": {},
        "有效": True,
        "明细来源": "条目登记（历史批次）",
    } for row in store.rows("ledger_entry")
        if str(row.get("批次编号", "")) == batch_id
        and str(row.get("条目状态")) == LedgerEntryStatus.STORED.value]


def trace_ledger(ledger_id: str) -> dict[str, Any]:
    """原提交、补交记录、条目状态与实际批次内容的完整回查。"""
    ledger = _ledger_row(ledger_id)
    if ledger is None:
        return {"ok": False, "message": f"清册 {ledger_id} 不存在", "清册编号": ledger_id}

    entries = [_entry_from_row(row).to_dict() for row in _ledger_entries(ledger_id)]
    submissions = [row for row in store.rows("ledger_submission")
                   if str(row.get("清册编号", "")) == str(ledger_id)]
    claims = [row for row in store.rows("ledger_claim")
              if str(row.get("清册编号", "")) == str(ledger_id)]
    batch_ids = sorted({str(row.get("批次编号", "")) for row in _ledger_entries(ledger_id)
                        if row.get("批次编号")})
    batches = []
    for bid in batch_ids:
        batch_row = next((r for r in store.rows("batch_store") if str(r.get("批次编号")) == bid), None)
        contents = _batch_records(bid)
        batches.append({
            "批次编号": bid,
            "站点编号": (batch_row or {}).get("站点编号", ""),
            "站点域名": (batch_row or {}).get("站点域名", ""),
            "状态": (batch_row or {}).get("status", ""),
            "数据行数": (batch_row or {}).get("数据行数", 0),
            "实际内容": contents,
            "明细可查": bool(contents),
            "说明": "" if contents else "历史批次仅保留批次级登记（来源作业/站点/行数），未保留逐条明细",
        })

    site_ids = sorted({row.get("站点编号", "") for row in entries if row.get("站点编号")})
    sites = []
    for sid in site_ids:
        site = site_by_id(sid)
        site_entries = [row for row in entries if row.get("站点编号") == sid]
        sites.append({
            "站点编号": sid,
            "站点域名": site.domain if site else "",
            "核验状态": "已验证" if (site and site.verified) else "待核验",
            "核验依据": site.verification_proof if site else "",
            "条目数": len(site_entries),
            "可领取": sum(1 for row in site_entries if row["条目状态"] == LedgerEntryStatus.CLAIMABLE.value),
            "待核": sum(1 for row in site_entries if row["条目状态"] == LedgerEntryStatus.PENDING.value),
            "已领取": sum(1 for row in site_entries if row["条目状态"] == LedgerEntryStatus.CLAIMED.value),
            "已入库": sum(1 for row in site_entries if row["条目状态"] == LedgerEntryStatus.STORED.value),
            "越权拦截": sum(1 for row in site_entries if row["条目状态"] == LedgerEntryStatus.BLOCKED.value),
        })

    # 历史批次兼容：没有清册编号的批次（早期作业直接落库）排在前面，便于回查。
    others = [row for row in store.rows("batch_store")
              if str(row.get("清册编号", "")) != str(ledger_id)]
    others.sort(key=lambda row: 0 if not str(row.get("清册编号", "")) else 1)

    return {
        "ok": True,
        "清册": dict(ledger),
        "站点": sites,
        "提交记录": [dict(row) for row in submissions],
        "领取记录": [dict(row) for row in claims],
        "条目": entries,
        "批次": batches,
        "历史批次": [_historical_batch(row) for row in others][:50],
    }


def _historical_batch(row: dict[str, Any]) -> dict[str, Any]:
    """兼容历史批次：没有清册编号时按来源作业与站点字段回查。"""
    return {
        "批次编号": row.get("批次编号", ""),
        "来源作业": row.get("来源作业", ""),
        "站点编号": row.get("站点编号", ""),
        "站点域名": row.get("站点域名", ""),
        "清册编号": row.get("清册编号", ""),
        "数据行数": row.get("数据行数", 0),
        "状态": row.get("status", ""),
    }


def site_source_inventory(ledger_id: str | None = None) -> list[dict[str, Any]]:
    """站点来源清册：按站点汇总条目、批次与实际落库内容。"""
    ledgers = ([_ledger_row(ledger_id)] if ledger_id else list(store.rows("source_ledger")))
    ledgers = [row for row in ledgers if row is not None]
    inventory: dict[str, dict[str, Any]] = {}

    for ledger in ledgers:
        lid = str(ledger.get("清册编号", ""))
        for entry in _ledger_entries(lid):
            sid = str(entry.get("站点编号", ""))
            bucket = inventory.setdefault(sid, {
                "站点编号": sid,
                "站点域名": entry.get("站点域名", ""),
                "清册编号": lid,
                "来源条目": [],
                "批次": [],
                "核验依据": entry.get("核验依据", ""),
            })
            bucket["来源条目"].append({
                "条目编号": entry.get("条目编号"),
                "来源URL": entry.get("来源URL"),
                "条目状态": entry.get("条目状态"),
                "提交来源": entry.get("提交来源"),
                "批次编号": entry.get("批次编号"),
                "核验依据": entry.get("核验依据"),
            })
            bid = str(entry.get("批次编号", ""))
            if bid and bid not in {b["批次编号"] for b in bucket["批次"]}:
                batch_row = next((r for r in store.rows("batch_store")
                                  if str(r.get("批次编号")) == bid), None)
                bucket["批次"].append({
                    "批次编号": bid,
                    "站点编号": (batch_row or {}).get("站点编号", sid),
                    "站点域名": (batch_row or {}).get("站点域名", ""),
                    "状态": (batch_row or {}).get("status", ""),
                    "实际内容": _batch_records(bid),
                })
    return list(inventory.values())


# ---------------------------------------------------------------------------
# 清册统计刷新
# ---------------------------------------------------------------------------

def _refresh_ledger(ledger_id: str) -> dict[str, Any]:
    ledger = _ledger_row(ledger_id)
    if ledger is None:
        return {}
    rows = _ledger_entries(ledger_id)
    counts = {state.value: 0 for state in LedgerEntryStatus}
    for row in rows:
        state = str(row.get("条目状态", LedgerEntryStatus.PENDING.value))
        counts[state] = counts.get(state, 0) + 1
    ledger["条目总数"] = len(rows)
    ledger["待核数"] = counts.get(LedgerEntryStatus.PENDING.value, 0)
    ledger["可领取数"] = counts.get(LedgerEntryStatus.CLAIMABLE.value, 0)
    ledger["已领取数"] = counts.get(LedgerEntryStatus.CLAIMED.value, 0)
    ledger["已入库数"] = counts.get(LedgerEntryStatus.STORED.value, 0)
    ledger["越权拦截数"] = counts.get(LedgerEntryStatus.BLOCKED.value, 0)
    ledger["站点范围"] = "，".join(sorted({str(row.get("站点编号", "")) for row in rows
                                         if row.get("站点编号")}))
    ledger["批次范围"] = "，".join(sorted({str(row.get("批次编号", "")) for row in rows
                                         if row.get("批次编号")}))
    if rows and counts.get(LedgerEntryStatus.STORED.value, 0) == len(rows):
        ledger["status"] = "已完成"
    elif counts.get(LedgerEntryStatus.CLAIMED.value, 0) or counts.get(LedgerEntryStatus.STORED.value, 0):
        ledger["status"] = "采集中"
    elif counts.get(LedgerEntryStatus.CLAIMABLE.value, 0) and counts.get(LedgerEntryStatus.PENDING.value, 0):
        ledger["status"] = "部分可领取"
    elif counts.get(LedgerEntryStatus.CLAIMABLE.value, 0):
        ledger["status"] = "可领取"
    else:
        ledger["status"] = "待核验"
    ledger["pending"] = counts.get(LedgerEntryStatus.PENDING.value, 0) + counts.get(LedgerEntryStatus.CLAIMABLE.value, 0) > 0
    ledger["abnormal"] = counts.get(LedgerEntryStatus.BLOCKED.value, 0) > 0
    return dict(ledger)


def refresh_all() -> None:
    for row in list(store.rows("source_ledger")):
        _refresh_ledger(str(row.get("清册编号", "")))


# ---------------------------------------------------------------------------
# 通用服务：列表接口与动作分派
# ---------------------------------------------------------------------------

class SourceLedgerService(BaseModuleService):
    MODULE = "source_ledger"
    ENTITY = "来源清册"
    OPERATION_TERM = "采集"
    LIST_FIELDS = ["清册编号", "清册名称", "站点范围", "条目总数", "待核数", "可领取数",
                   "已领取数", "已入库数", "越权拦截数", "批次范围", "创建人", "创建时间"]
    REQUIRED_FIELDS = ["清册编号", "清册名称"]
    STATUS_ORDER = ["待核验", "部分可领取", "可领取", "采集中", "已完成"]
    ACTION_RULES = {"刷新清册": "待核验"}
    NEGATIVE_ACTIONS = []

    def create_entry(self, values: dict[str, Any]):
        """登记清册时若带站点编号/来源URL，直接走提交判定。"""
        name = str(values.get("清册名称") or values.get("名称") or "").strip()
        if not name:
            return None, ["清册编号/清册名称"]
        ledger_id = str(values.get("清册编号") or "").strip() or None
        ledger = create_ledger(name, str(values.get("创建人") or "值班员"), ledger_id)
        urls = values.get("来源URL") or values.get("入口URL") or values.get("URL")
        site_id = str(values.get("站点编号") or "").strip()
        if urls:
            submit_entries(str(ledger["清册编号"]), site_id, str(urls),
                           operator=str(values.get("创建人") or "值班员"))
        return _refresh_ledger(str(ledger["清册编号"])), []

    def run_action(self, entry_id: int, action: str):
        row = store.find(self.MODULE, entry_id)
        if row is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        ledger_id = str(row.get("清册编号", ""))
        if action in ("领取任务", "领取", "调度领取"):
            result = claim_ledger(ledger_id)
            return _refresh_ledger(ledger_id), str(result.get("message", "已领取"))
        if action in ("落库", "入库", "标记入库"):
            result = store_ledger(ledger_id)
            return _refresh_ledger(ledger_id), str(result.get("message", "已入库"))
        if action in ("刷新清册", "刷新"):
            return _refresh_ledger(ledger_id), "清册统计已刷新"
        return super().run_action(entry_id, action)


service = SourceLedgerService()
