"""__NAME__领域服务：字段、状态和动作来自业务配置，数据处理骨架由基类复用。"""
from __future__ import annotations

from app.services.base import BaseModuleService


class CrawlJobService(BaseModuleService):
    MODULE = "crawl_job"
    ENTITY = "采集作业"
    OPERATION_TERM = "采集"
    LIST_FIELDS = ["作业编号", "目标站点", "入口URL", "最大深度", "并发线程", "调度策略", "完成页数", "失败页数",
                   "清册编号", "站点编号", "核验状态", "核验依据"]
    REQUIRED_FIELDS = ["作业编号", "目标站点", "入口URL"]
    STATUS_ORDER = ["待执行", "采集执行", "已完成", "部分失败"]
    ACTION_RULES = {"启动采集": "采集执行", "暂停作业": "已完成", "标记完成": "部分失败"}
    NEGATIVE_ACTIONS = []
