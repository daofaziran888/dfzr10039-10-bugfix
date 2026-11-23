import { request } from './client'
import type {
  ActionResult,
  LedgerClaimResult,
  LedgerSubmitResult,
  LedgerTraceResult,
} from '@/data/types'

/** 站点来源清册接口：提交、补证、领取、落库与回查。 */
async function postJson<T>(path: string, body?: unknown): Promise<T> {
  const response = await request(path, {
    method: 'POST',
    body: JSON.stringify(body ?? {}),
  })
  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = payload && typeof payload.detail === 'string' ? payload.detail : '清册操作失败'
    throw new Error(detail)
  }
  return payload as T
}

export type LedgerEntrySpec = {
  url: string
  site_id: string
  note?: string
}

export async function submitLedger(payload: {
  ledger_id?: string
  name: string
  entries: LedgerEntrySpec[]
  origin?: string
  operator?: string
}): Promise<LedgerSubmitResult> {
  return await postJson<LedgerSubmitResult>('/api/ledger/submit', payload)
}

export async function submitSingleAddress(payload: {
  ledger_id?: string
  name: string
  site_id: string
  urls: string[]
  operator?: string
}): Promise<LedgerSubmitResult> {
  return await postJson<LedgerSubmitResult>('/api/ledger/single', payload)
}

export async function submitProof(payload: {
  ledger_id: string
  site_id: string
  proof: string
  urls?: string[]
  operator?: string
}): Promise<Record<string, unknown>> {
  return await postJson('/api/ledger/proof', payload)
}

export async function claimLedger(ledgerId: string): Promise<LedgerClaimResult> {
  return await postJson<LedgerClaimResult>(`/api/ledger/${encodeURIComponent(ledgerId)}/claim`)
}

export async function storeLedger(ledgerId: string): Promise<Record<string, unknown>> {
  return await postJson(`/api/ledger/${encodeURIComponent(ledgerId)}/store`)
}

export async function traceLedger(ledgerId: string): Promise<LedgerTraceResult> {
  const response = await request(`/api/ledger/${encodeURIComponent(ledgerId)}/trace`)
  if (!response.ok) throw new Error('清册回查失败')
  return (await response.json()) as LedgerTraceResult
}

export async function listInventory(
  ledgerId?: string,
): Promise<{ ok: boolean; items: Record<string, unknown>[] }> {
  const query = ledgerId ? `?ledger_id=${encodeURIComponent(ledgerId)}` : ''
  const response = await request(`/api/ledger/inventory${query}`)
  if (!response.ok) throw new Error('站点来源清册读取失败')
  return (await response.json()) as { ok: boolean; items: Record<string, unknown>[] }
}

export async function runLedgerAction(id: number, action: string): Promise<ActionResult> {
  const response = await request(`/api/source_ledger/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return (await response.json()) as ActionResult
}
