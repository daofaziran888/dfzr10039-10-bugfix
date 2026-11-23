/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  [field: string]: string | number | boolean
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
}

export type PageResult = {
  items: EntryRow[]
  total: number
  page: number
  size: number
}

export type ActionResult = {
  ok: boolean
  message: string
}

export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

/** 站点来源清册：提交、领取、补证、落库与回查的接口返回。 */
export type LedgerSubmitResult = {
  ok: boolean
  message: string
  清册编号: string
  提交: LedgerSubmitReceipt[]
  清册: Record<string, string | number | boolean>
}

export type LedgerSubmitReceipt = {
  ok: boolean
  message: string
  清册编号: string
  提交编号?: string
  新增?: string[]
  可领取?: { 来源URL: string; 站点编号: string }[]
  待核?: { 来源URL: string; 站点编号: string; 说明: string }[]
  拦截?: { 来源URL: string; 站点编号: string; 说明: string }[]
  重复?: string[]
}

export type LedgerClaimResult = {
  ok: boolean
  清册编号: string
  可执行: boolean
  可执行站点: string[]
  说明: string
  本次领取地址: LedgerEntryView[]
  越权拦截: { 条目编号: string; 来源URL: string; 站点编号: string; 拦截说明: string }[]
  等待核验: { 条目编号: string; 来源URL: string; 站点编号: string; 站点域名: string; 待核说明: string }[]
  站点批次: Record<string, string>
  批次: { 批次编号: string; 站点编号: string; 站点域名: string; 数据行数: number; 状态: string }[]
}

export type LedgerEntryView = {
  条目编号: string
  清册编号: string
  来源URL: string
  地址主机: string
  站点编号: string
  站点域名: string
  条目状态: string
  提交来源: string
  核验依据: string
  批次编号: string
  作业编号: string
  处理说明: string
  实际来源站点: string
}

export type LedgerTraceResult = {
  ok: boolean
  清册: Record<string, string | number | boolean>
  站点: {
    站点编号: string
    站点域名: string
    核验状态: string
    核验依据: string
    条目数: number
    可领取: number
    待核: number
    已领取: number
    已入库: number
    越权拦截: number
  }[]
  提交记录: Record<string, string | number>[]
  领取记录: Record<string, string | number>[]
  条目: LedgerEntryView[]
  批次: {
    批次编号: string
    站点编号: string
    站点域名: string
    状态: string
    数据行数: number
    实际内容: {
      来源URL: string
      站点编号: string
      清册条目: string
      核验依据: string
      字段: Record<string, string>
      有效: boolean
    }[]
    明细可查: boolean
    说明: string
  }[]
  历史批次: Record<string, string | number>[]
}
