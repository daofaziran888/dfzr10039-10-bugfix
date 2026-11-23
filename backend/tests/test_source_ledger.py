"""采集入口站点归属与站点来源清册测试。

覆盖值班场景：只有 A 站核验通过，混合来源清册里 B 的网址不能借 A 的授权入库；
先单独给 B 提一个地址，再走混合提交；补证前领取、补证后继续同一份清册。
"""
from __future__ import annotations

import pytest

from app.integration import partition_urls_by_site, run_crawl_pipeline
from app.models import SiteConfig, domain_matches, normalize_host
from app.services import source_ledger as ledger
from app.store import store


# ---------------------------------------------------------------------------
# 测试数据
# ---------------------------------------------------------------------------

SITE_A = {
    "id": 1, "status": "已验证", "pending": False, "abnormal": False,
    "站点编号": "SITE-A", "站点域名": "a.example.com", "站点类型": "新闻门户",
    "Robots协议": "allow", "站点语言": "zh-CN", "地域归属": "CN",
    "接入方式": "direct", "注册时间": "2026-11-01",
    "核验证明": "ICP-A-2026", "核验时间": "2026-11-01T09:00:00+00:00",
}

SITE_B = {
    "id": 2, "status": "待验证", "pending": True, "abnormal": False,
    "站点编号": "SITE-B", "站点域名": "b.example.net", "站点类型": "内容平台",
    "Robots协议": "allow", "站点语言": "zh-CN", "地域归属": "CN",
    "接入方式": "direct", "注册时间": "2026-11-02",
    "核验证明": "", "核验时间": "",
}

EXTRACT_RULE = {
    "id": 1, "status": "生效中", "pending": False, "abnormal": False,
    "规则编号": "CONT-LEDGER", "目标站点": "a.example.com",
    "XPath选择器": "//article", "字段映射": "title,body",
    "数据类型": "string", "清洗正则": "", "抽取顺序": "1", "规则状态": "生效中",
}

CLEAN_RULE = {
    "id": 1, "status": "生效中", "pending": False, "abnormal": False,
    "规则编号": "FIEL-LEDGER", "目标字段": "title", "清洗类型": "去空格",
    "处理表达式": "", "空值策略": "skip", "校验范围": "",
    "清洗顺序": "1", "规则状态": "生效中",
}


@pytest.fixture()
def sites() -> None:
    """把 A/B 站点写入仓库，保证测试互不依赖 seed 顺序。"""
    for site in (SITE_A, SITE_B):
        row = next((r for r in store.rows("crawl_site")
                    if r.get("站点编号") == site["站点编号"]), None)
        if row is None:
            store.rows("crawl_site").append({
                **site, "id": max((int(r.get("id", 0)) for r in store.rows("crawl_site")),
                                  default=0) + 1,
            })
        else:
            row.update(site)
    store.rows("content_extract").append(dict(EXTRACT_RULE))
    store.rows("field_clean").append(dict(CLEAN_RULE))


def _new_ledger(name: str) -> str:
    return str(ledger.create_ledger(name)["清册编号"])


def _status_of(ledger_id: str, url: str) -> str:
    for row in ledger._ledger_entries(ledger_id):
        if row.get("来源URL") == url:
            return str(row.get("条目状态"))
    raise AssertionError(f"{url} 不在清册 {ledger_id}")


# ---------------------------------------------------------------------------
# 网址与站点归属
# ---------------------------------------------------------------------------

def test_domain_matching_uses_label_boundary() -> None:
    assert domain_matches("a.example.com", "a.example.com")
    assert domain_matches("news.a.example.com", "a.example.com")
    assert not domain_matches("evila.example.com", "a.example.com")
    assert not domain_matches("b.example.net", "a.example.com")


def test_normalize_host_strips_port_and_scheme() -> None:
    assert normalize_host("https://A.Example.com:8443/x") == "a.example.com"
    assert normalize_host("a.example.com/path") == "a.example.com"
    assert normalize_host("") == ""


def test_partition_rejects_cross_site_urls() -> None:
    site_a = SiteConfig.from_seed_row(SITE_A)
    site_b = SiteConfig.from_seed_row(SITE_B)
    owned, rejected = partition_urls_by_site(
        ["https://a.example.com/1", "https://b.example.net/9"],
        site_a, [site_a, site_b],
    )
    assert owned == ["https://a.example.com/1"]
    assert len(rejected) == 1
    assert "SITE-B" in rejected[0]["reason"]


def test_pipeline_never_writes_foreign_urls_into_site_batch() -> None:
    """回归：A 已验证，作业里混入 B 的地址，B 的页面不能进 A 的批次。"""
    job = {
        "id": 1, "status": "待执行", "pending": True, "abnormal": False,
        "作业编号": "JOB-ATTR", "目标站点": "a.example.com",
        "入口URL": "https://a.example.com/1,https://b.example.net/9",
        "最大深度": "3", "并发线程": "4", "调度策略": "interval",
        "完成页数": "0", "失败页数": "0",
    }
    batch = run_crawl_pipeline(
        site_row=SITE_A, job_row=job,
        extract_rows=[EXTRACT_RULE], clean_rows=[CLEAN_RULE],
        known_site_rows=[SITE_A, SITE_B],
    )
    assert batch is not None
    urls = [record.source_url for record in batch.records]
    assert urls == ["https://a.example.com/1"]
    assert all(record.site_id == "SITE-A" for record in batch.records)
    assert batch.site_domain == "a.example.com"


def test_pipeline_returns_no_batch_when_only_foreign_urls() -> None:
    job = {
        "id": 2, "status": "待执行", "pending": True, "abnormal": False,
        "作业编号": "JOB-ATTR-2", "目标站点": "a.example.com",
        "入口URL": "https://b.example.net/9",
        "最大深度": "3", "并发线程": "4", "调度策略": "interval",
        "完成页数": "0", "失败页数": "0",
    }
    assert run_crawl_pipeline(
        site_row=SITE_A, job_row=job,
        extract_rows=[EXTRACT_RULE], clean_rows=[CLEAN_RULE],
        known_site_rows=[SITE_A, SITE_B],
    ) is None


# ---------------------------------------------------------------------------
# 提交：单独提交与混合提交
# ---------------------------------------------------------------------------

def test_single_submission_cannot_use_site_a_for_site_b_url(sites) -> None:
    """先拿已验证的 A 给未确认的 B 单独提交一个地址。"""
    ledger_id = _new_ledger("A 授权单提 B 地址")
    result = ledger.submit_single_address(
        "A 授权单提 B 地址", "SITE-A", "https://b.example.net/post/1", ledger_id=ledger_id)
    assert result["ok"]
    assert result["提交"][0]["拦截"], "B 的地址不能借 A 的授权"
    assert _status_of(ledger_id, "https://b.example.net/post/1") == "已拦截"

    # 该地址不应生成任何批次，也不应产生 A 的作业入口。
    assert not [b for b in store.rows("batch_store") if b.get("清册编号") == ledger_id]
    assert ledger.claim_ledger(ledger_id)["可执行站点"] == []


def test_mixed_submission_routes_each_url_to_its_own_site(sites) -> None:
    ledger_id = _new_ledger("混合清册")
    result = ledger.submit_mixed_ledger("混合清册", [
        {"url": "https://a.example.com/article/1", "site_id": "SITE-A"},
        {"url": "https://b.example.net/post/9", "site_id": "SITE-B"},
        {"url": "https://b.example.net/post/10", "site_id": "SITE-A"},
    ], ledger_id=ledger_id)

    assert result["ok"]
    assert _status_of(ledger_id, "https://a.example.com/article/1") == "可领取"
    assert _status_of(ledger_id, "https://b.example.net/post/9") == "待核"
    assert _status_of(ledger_id, "https://b.example.net/post/10") == "已拦截"

    # 待核地址只暴露待核说明，不生成可执行作业入口。
    claim = ledger.claim_ledger(ledger_id)
    pending_urls = {item["来源URL"] for item in claim["等待核验"]}
    assert pending_urls == {"https://b.example.net/post/9"}


def test_blocked_entry_does_not_leak_into_site_b_job(sites) -> None:
    ledger_id = _new_ledger("拦截不串批")
    ledger.submit_mixed_ledger("拦截不串批", [
        {"url": "https://b.example.net/post/9", "site_id": "SITE-B"},
        {"url": "https://b.example.net/post/10", "site_id": "SITE-A"},
    ], ledger_id=ledger_id)
    job_b = next((row for row in store.rows("crawl_job")
                  if row.get("清册编号") == ledger_id and row.get("站点编号") == "SITE-B"), None)
    assert job_b is not None
    # SITE-B 的作业只取归属 B 且未被拦截的地址。
    assert "https://b.example.net/post/10" not in str(job_b["入口URL"])


# ---------------------------------------------------------------------------
# 领取：补证前 / 补证后
# ---------------------------------------------------------------------------

def test_claim_before_proof_only_consumes_verified_site(sites) -> None:
    ledger_id = _new_ledger("补证前领取")
    ledger.submit_mixed_ledger("补证前领取", [
        {"url": "https://a.example.com/article/1", "site_id": "SITE-A"},
        {"url": "https://b.example.net/post/9", "site_id": "SITE-B"},
    ], ledger_id=ledger_id)

    claim = ledger.claim_ledger(ledger_id)
    assert claim["可执行站点"] == ["SITE-A"]
    assert claim["可执行"] is True
    assert [e["来源URL"] for e in claim["本次领取地址"]] == ["https://a.example.com/article/1"]
    assert claim["站点批次"]["SITE-A"].startswith("BATCH-")
    assert "SITE-B" not in claim["站点批次"]
    # 等待范围只包含 B 的待核地址。
    assert [item["站点编号"] for item in claim["等待核验"]] == ["SITE-B"]
    assert claim["说明"]


def test_proof_continues_same_ledger_without_rerunning_claimed(sites) -> None:
    ledger_id = _new_ledger("补证续账")
    ledger.submit_mixed_ledger("补证续账", [
        {"url": "https://a.example.com/article/1", "site_id": "SITE-A"},
        {"url": "https://b.example.net/post/9", "site_id": "SITE-B"},
    ], ledger_id=ledger_id)

    first = ledger.claim_ledger(ledger_id)
    claimed_url = first["本次领取地址"][0]["来源URL"]
    first_batch = first["站点批次"]["SITE-A"]

    proof = ledger.attach_verification_proof(
        ledger_id, "SITE-B", "ICP-B-2026 域名归属证明")
    assert proof["ok"]
    assert "https://b.example.net/post/9" in proof["升级地址"]
    # 已领取的 A 地址不因补证被重跑。
    assert claimed_url not in proof["升级地址"]

    second = ledger.claim_ledger(ledger_id)
    assert second["可执行站点"] == ["SITE-B"]
    assert [e["来源URL"] for e in second["本次领取地址"]] == ["https://b.example.net/post/9"]
    assert second["站点批次"]["SITE-B"] != first_batch

    # 再次领取不会重复消费任何已领地址。
    third = ledger.claim_ledger(ledger_id)
    assert third["本次领取地址"] == []
    assert third["可执行"] is False


def test_proof_does_not_upgrade_unrelated_pending_site(sites) -> None:
    ledger_id = _new_ledger("补证只升本站")
    ledger.submit_mixed_ledger("补证只升本站", [
        {"url": "https://b.example.net/post/9", "site_id": "SITE-B"},
    ], ledger_id=ledger_id)
    proof = ledger.attach_verification_proof(ledger_id, "SITE-A", "ICP-A-2026")
    # A 的证明不能替 B 的地址解锁。
    assert "https://b.example.net/post/9" not in proof["升级地址"]
    assert _status_of(ledger_id, "https://b.example.net/post/9") == "待核"


def test_proof_never_reruns_already_stored_addresses(sites) -> None:
    ledger_id = _new_ledger("补证不重跑")
    ledger.submit_mixed_ledger("补证不重跑", [
        {"url": "https://a.example.com/article/1", "site_id": "SITE-A"},
        {"url": "https://b.example.net/post/9", "site_id": "SITE-B"},
    ], ledger_id=ledger_id)
    # 先把 A 采完并落库。
    ledger.claim_ledger(ledger_id)
    ledger.store_ledger(ledger_id)
    assert _status_of(ledger_id, "https://a.example.com/article/1") == "已入库"

    proof = ledger.attach_verification_proof(
        ledger_id, "SITE-B", "ICP-B-2026", urls=["https://a.example.com/article/1"])
    assert proof["重跑拦截"] == ["https://a.example.com/article/1"]
    assert "https://a.example.com/article/1" not in proof["升级地址"]
    assert _status_of(ledger_id, "https://a.example.com/article/1") == "已入库"


# ---------------------------------------------------------------------------
# 落库、批次真实性、回查
# ---------------------------------------------------------------------------

def test_batches_are_per_site_with_real_contents(sites) -> None:
    ledger_id = _new_ledger("分站点批次")
    ledger.submit_mixed_ledger("分站点批次", [
        {"url": "https://a.example.com/article/1", "site_id": "SITE-A"},
        {"url": "https://a.example.com/article/2", "site_id": "SITE-A"},
        {"url": "https://b.example.net/post/9", "site_id": "SITE-B"},
        {"url": "https://b.example.net/post/10", "site_id": "SITE-B"},
    ], ledger_id=ledger_id)
    ledger.attach_verification_proof(ledger_id, "SITE-B", "ICP-B-2026")

    claim = ledger.claim_ledger(ledger_id)
    assert set(claim["站点批次"]) == {"SITE-A", "SITE-B"}

    stored = ledger.store_ledger(ledger_id)
    assert stored["ok"]

    trace = ledger.trace_ledger(ledger_id)
    by_site = {batch["站点编号"]: batch for batch in trace["批次"]}
    assert set(by_site) == {"SITE-A", "SITE-B"}
    a_urls = {item["来源URL"] for item in by_site["SITE-A"]["实际内容"]}
    b_urls = {item["来源URL"] for item in by_site["SITE-B"]["实际内容"]}
    assert all("a.example.com" in url for url in a_urls)
    assert all("b.example.net" in url for url in b_urls)
    assert a_urls.isdisjoint(b_urls)
    assert len(a_urls) == 2 and len(b_urls) == 2

    # A 的任何批次都不含 B 的地址。
    for batch in store.rows("batch_store"):
        if str(batch.get("清册编号")) == ledger_id and str(batch.get("站点编号")) == "SITE-A":
            contents = ledger._batch_records(str(batch["批次编号"]))
            assert all("b.example.net" not in item["来源URL"] for item in contents)


def test_every_stored_item_keeps_source_and_proof(sites) -> None:
    ledger_id = _new_ledger("来源依据留痕")
    ledger.submit_mixed_ledger("来源依据留痕", [
        {"url": "https://a.example.com/article/1", "site_id": "SITE-A"},
        {"url": "https://b.example.net/post/9", "site_id": "SITE-B"},
    ], ledger_id=ledger_id)
    ledger.attach_verification_proof(ledger_id, "SITE-B", "ICP-B-2026")
    ledger.claim_ledger(ledger_id)
    ledger.store_ledger(ledger_id)

    trace = ledger.trace_ledger(ledger_id)
    for batch in trace["批次"]:
        for content in batch["实际内容"]:
            assert content["来源URL"]
            assert content["站点编号"] == batch["站点编号"]
            assert content["清册条目"]
            assert content["核验依据"], "落库项必须保留核验依据"

    inventory = ledger.site_source_inventory(ledger_id)
    assert {row["站点编号"] for row in inventory} == {"SITE-A", "SITE-B"}
    for row in inventory:
        for batch in row["批次"]:
            assert batch["站点编号"] == row["站点编号"]
            assert all(batch["批次编号"] != "" for batch in row["批次"])


def test_trace_has_original_and_supplement_records(sites) -> None:
    ledger_id = _new_ledger("提交可回查")
    ledger.submit_mixed_ledger("提交可回查", [
        {"url": "https://a.example.com/article/1", "site_id": "SITE-A"},
    ], ledger_id=ledger_id)
    ledger.submit_mixed_ledger("提交可回查", [
        {"url": "https://b.example.net/post/9", "site_id": "SITE-B"},
    ], ledger_id=ledger_id, origin="补交")

    trace = ledger.trace_ledger(ledger_id)
    origins = [record["提交类型"] for record in trace["提交记录"]]
    assert "原始提交" in origins
    assert "补交" in origins
    assert trace["提交记录"], "原提交与补交记录都必须能回查"


def test_jobs_and_batches_read_from_same_ledger(sites) -> None:
    """作业与批次都从同一份清册取数，作业入口不含越权地址。"""
    ledger_id = _new_ledger("同源取数")
    ledger.submit_mixed_ledger("同源取数", [
        {"url": "https://a.example.com/article/1", "site_id": "SITE-A"},
        {"url": "https://b.example.net/post/9", "site_id": "SITE-A"},  # 越权
    ], ledger_id=ledger_id)
    claim = ledger.claim_ledger(ledger_id)
    assert claim["越权拦截"]

    job = next(row for row in store.rows("crawl_job")
               if row.get("清册编号") == ledger_id and row.get("站点编号") == "SITE-A")
    entries_a = {row["来源URL"] for row in ledger._ledger_entries(ledger_id)
                 if row.get("站点编号") == "SITE-A"
                 and row.get("条目状态") != "已拦截"}
    assert set(str(job["入口URL"]).split(",")) == entries_a
    assert "https://b.example.net/post/9" not in str(job["入口URL"])


def test_site_verification_syncs_to_job_checklist(sites) -> None:
    ledger_id = _new_ledger("核验同步作业")
    ledger.submit_mixed_ledger("核验同步作业", [
        {"url": "https://b.example.net/post/9", "site_id": "SITE-B"},
    ], ledger_id=ledger_id)
    job = next(row for row in store.rows("crawl_job")
               if row.get("清册编号") == ledger_id and row.get("站点编号") == "SITE-B")
    assert job["核验状态"] == "待核验"
    assert job["status"] == "待执行"

    ledger.attach_verification_proof(ledger_id, "SITE-B", "ICP-B-2026")
    job = next(row for row in store.rows("crawl_job")
               if row.get("清册编号") == ledger_id and row.get("站点编号") == "SITE-B")
    assert job["核验状态"] == "已验证"
    assert job["核验依据"] == "ICP-B-2026"


def test_legacy_batch_sources_are_queryable(sites) -> None:
    """兼容历史批次：没有清册编号的批次仍可按来源作业与站点回查。"""
    store.rows("batch_store").append({
        "id": 9999, "批次编号": "BATCH-LEGACY-001", "来源作业": "JOB-LEGACY-001",
        "数据行数": 3, "存储路径": "/legacy/", "压缩格式": "gzip",
        "Schema版本": "v1", "校验摘要": "old", "入库时间": "2026-09-01",
        "站点编号": "SITE-A", "站点域名": "a.example.com", "清册编号": "",
        "status": "校验通过", "pending": False, "abnormal": False,
    })
    ledger_id = _new_ledger("历史兼容")
    trace = ledger.trace_ledger(ledger_id)
    legacy = [row for row in trace["历史批次"] if row["批次编号"] == "BATCH-LEGACY-001"]
    assert legacy, "历史批次应可回查"
    assert legacy[0]["站点编号"] == "SITE-A"


def test_duplicate_submission_is_skipped(sites) -> None:
    ledger_id = _new_ledger("重复提交")
    ledger.submit_mixed_ledger("重复提交", [
        {"url": "https://a.example.com/article/1", "site_id": "SITE-A"},
    ], ledger_id=ledger_id)
    again = ledger.submit_mixed_ledger("重复提交", [
        {"url": "https://a.example.com/article/1", "site_id": "SITE-A"},
    ], ledger_id=ledger_id)
    assert again["提交"][0]["重复"] == ["https://a.example.com/article/1"]
    assert len(ledger._ledger_entries(ledger_id)) == 1


def test_claiming_empty_ledger_does_not_execute(sites) -> None:
    ledger_id = _new_ledger("空清册")
    ledger.submit_mixed_ledger("空清册", [
        {"url": "https://b.example.net/post/9", "site_id": "SITE-B"},
    ], ledger_id=ledger_id)
    claim = ledger.claim_ledger(ledger_id)
    assert claim["可执行"] is False
    assert claim["本次领取地址"] == []
    assert claim["等待核验"]
    assert not [b for b in store.rows("batch_store") if b.get("清册编号") == ledger_id]


def test_claim_does_not_report_site_without_real_records(sites) -> None:
    """可领取条目在本轮全被拦截时，不应把该站点报成可执行。"""
    ledger_id = _new_ledger("空批次不成站")
    # 强行注入一条错放的可领取条目（模拟历史脏数据绕过提交校验）。
    ledger.create_ledger("空批次不成站", ledger_id=ledger_id)
    store.rows("ledger_entry").append({
        "id": 90001, "status": "可领取", "pending": True, "abnormal": False,
        "条目编号": "ENT-DIRTY-0001", "清册编号": ledger_id,
        "来源URL": "https://b.example.net/post/77", "地址主机": "b.example.net",
        "站点编号": "SITE-A", "站点域名": "a.example.com",
        "条目状态": "可领取", "提交来源": "原始提交",
        "提交时间": "2026-11-20T09:30:00+00:00", "核验时间": "", "核验依据": "",
        "批次编号": "", "作业编号": "", "处理说明": "",
        "领取时间": "", "入库时间": "", "实际来源站点": "SITE-A",
    })

    claim = ledger.claim_ledger(ledger_id)
    assert claim["可执行"] is False
    assert claim["可执行站点"] == []
    assert claim["本次领取地址"] == []
    assert claim["站点批次"] == {}
    assert claim["越权拦截"], "错放地址应被拦截并说明归属"
    assert not [b for b in store.rows("batch_store") if b.get("清册编号") == ledger_id]

