"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class CrawlSiteService(BaseModuleService):
    MODULE = "crawl_site"
    ENTITY = "目标站点"
    OPERATION_TERM = "采集"
    LIST_FIELDS = ["站点编号", "站点域名", "站点类型", "Robots协议", "站点语言", "地域归属", "接入方式", "注册时间",
                   "核验证明", "核验时间"]
    REQUIRED_FIELDS = ["站点编号", "站点域名", "站点类型"]
    STATUS_ORDER = ["待验证", "已验证", "站点变更", "已停采"]
    ACTION_RULES = {"验证站点": "已验证", "标记变更": "站点变更", "停采站点": "已停采"}
    NEGATIVE_ACTIONS = []
