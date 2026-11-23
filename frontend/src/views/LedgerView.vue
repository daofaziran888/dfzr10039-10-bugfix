<template>
  <section class="page" data-module="source_ledger">
    <header class="page-head">
      <div>
        <h2>站点来源清册</h2>
        <p class="page-desc">
          按站点归属管理地址：越权网址直接拦截，未核验站点只能看待核说明，领取只消费已有资格的地址。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" data-testid="ledger-refresh" type="button" @click="reloadAll">刷新</button>
      </div>
    </header>

    <p v-if="errorMessage" class="error-text" data-testid="ledger-error">{{ errorMessage }}</p>
    <p v-if="notice" class="notice-text" data-testid="ledger-notice">{{ notice }}</p>

    <div class="ledger-grid">
      <!-- 提交 -->
      <article class="panel">
        <h3>① 提交地址</h3>
        <p class="panel-hint">
          单独提交：给指定站点提交一个地址；混合提交：每行「站点编号 网址」，逐行按站点归属判定。
        </p>
        <label class="create-field">
          <span>清册编号（留空则新建）</span>
          <input v-model="submitForm.ledger_id" data-testid="submit-ledger-id" placeholder="LEDG-0001" />
        </label>
        <label class="create-field">
          <span>清册名称</span>
          <input v-model="submitForm.name" data-testid="submit-ledger-name" placeholder="值班混合来源清册" />
        </label>
        <label class="create-field">
          <span>单独提交站点编号</span>
          <input v-model="submitForm.site_id" data-testid="submit-site-id" placeholder="SITE-A" />
        </label>
        <label class="create-field">
          <span>单独提交网址</span>
          <input v-model="submitForm.single_url" data-testid="submit-single-url" placeholder="https://b.example.net/post/1" />
        </label>
        <div class="page-actions">
          <button class="btn" data-testid="submit-single" type="button" :disabled="busy" @click="doSubmitSingle">
            单独提交
          </button>
        </div>
        <label class="create-field">
          <span>混合提交（每行：站点编号 网址）</span>
          <textarea v-model="submitForm.mixed" data-testid="submit-mixed" rows="4" placeholder="SITE-A https://a.example.com/article/1&#10;SITE-B https://b.example.net/post/9"></textarea>
        </label>
        <div class="page-actions">
          <button class="btn primary" data-testid="submit-mixed-button" type="button" :disabled="busy" @click="doSubmitMixed">
            混合提交
          </button>
        </div>
      </article>

      <!-- 清册 -->
      <article class="panel">
        <h3>② 清册与动作</h3>
        <table class="data-table">
          <thead>
            <tr>
              <th>清册编号</th><th>名称</th><th>状态</th><th>待核</th><th>可领取</th><th>已领取</th><th>已入库</th><th>拦截</th><th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in ledgers" :key="String(row.id)" :data-testid="`ledger-row-${row.清册编号}`">
              <td>
                <button class="link" type="button" @click="selectLedger(String(row.清册编号))">
                  {{ row.清册编号 }}
                </button>
              </td>
              <td>{{ row.清册名称 }}</td>
              <td>{{ row.status }}</td>
              <td>{{ row.待核数 }}</td>
              <td>{{ row.可领取数 }}</td>
              <td>{{ row.已领取数 }}</td>
              <td>{{ row.已入库数 }}</td>
              <td>{{ row.越权拦截数 }}</td>
              <td class="row-actions">
                <button class="link" :data-testid="`action-${row.清册编号}-claim`" type="button" :disabled="busy" @click="doClaim(String(row.清册编号))">领取任务</button>
                <button class="link" :data-testid="`action-${row.清册编号}-store`" type="button" :disabled="busy" @click="doStore(String(row.清册编号))">落库</button>
                <button class="link" :data-testid="`action-${row.清册编号}-trace`" type="button" @click="selectLedger(String(row.清册编号))">回查</button>
              </td>
            </tr>
            <tr v-if="!ledgers.length">
              <td colspan="9" class="empty-state">暂无清册，先提交一份地址</td>
            </tr>
          </tbody>
        </table>

        <div v-if="activeLedger" class="proof-box">
          <h3>③ 补交核验证明</h3>
          <p class="panel-hint">只升级该站点的待核地址；已领取、已入库的地址不会重跑。</p>
          <label class="create-field">
            <span>站点编号</span>
            <input v-model="proofForm.site_id" data-testid="proof-site-id" placeholder="SITE-B" />
          </label>
          <label class="create-field">
            <span>核验依据</span>
            <input v-model="proofForm.proof" data-testid="proof-text" placeholder="ICP-B-2026 域名归属证明" />
          </label>
          <button class="btn primary" data-testid="submit-proof" type="button" :disabled="busy" @click="doProof">
            补交证明
          </button>
        </div>
      </article>
    </div>

    <!-- 领取结果 -->
    <article v-if="activeLedger" class="panel" data-testid="claim-result">
      <h3>④ 当前清册：{{ activeLedger }}</h3>
      <p class="panel-hint" data-testid="claim-summary">{{ claimSummary }}</p>
      <div class="ledger-grid">
        <div>
          <h4>本次可执行</h4>
          <p data-testid="executable-sites">{{ executableText }}</p>
          <table class="data-table">
            <thead><tr><th>站点</th><th>批次</th></tr></thead>
            <tbody>
              <tr v-for="(batchId, siteId) in siteBatches" :key="siteId">
                <td>{{ siteId }}</td><td>{{ batchId }}</td>
              </tr>
              <tr v-if="!Object.keys(siteBatches).length"><td colspan="2" class="empty-state">暂无</td></tr>
            </tbody>
          </table>
        </div>
        <div>
          <h4>待核说明（不可领取）</h4>
          <table class="data-table" data-testid="waiting-table">
            <thead><tr><th>站点</th><th>网址</th><th>待核说明</th></tr></thead>
            <tbody>
              <tr v-for="item in waiting" :key="item.条目编号">
                <td>{{ item.站点编号 }}</td><td>{{ item.来源URL }}</td><td>{{ item.待核说明 }}</td>
              </tr>
              <tr v-if="!waiting.length"><td colspan="3" class="empty-state">无待核地址</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </article>

    <!-- 回查 -->
    <article v-if="trace" class="panel" data-testid="trace-panel">
      <h3>⑤ 可追溯来源清册</h3>
      <p class="panel-hint">原提交与补交记录、各条目状态，以及实际落库批次的真实内容。</p>
      <table class="data-table">
        <thead><tr><th>站点</th><th>核验状态</th><th>核验依据</th><th>条目</th><th>可领取</th><th>待核</th><th>已领取</th><th>已入库</th><th>拦截</th></tr></thead>
        <tbody>
          <tr v-for="site in trace.站点" :key="site.站点编号">
            <td>{{ site.站点编号 }}（{{ site.站点域名 }}）</td>
            <td>{{ site.核验状态 }}</td>
            <td>{{ site.核验依据 || '—' }}</td>
            <td>{{ site.条目数 }}</td>
            <td>{{ site.可领取 }}</td>
            <td>{{ site.待核 }}</td>
            <td>{{ site.已领取 }}</td>
            <td>{{ site.已入库 }}</td>
            <td>{{ site.越权拦截 }}</td>
          </tr>
        </tbody>
      </table>

      <h4>提交记录</h4>
      <table class="data-table">
        <thead><tr><th>提交编号</th><th>类型</th><th>站点</th><th>新增</th><th>可领取</th><th>待核</th><th>拦截</th><th>提交时间</th></tr></thead>
        <tbody>
          <tr v-for="item in trace.提交记录" :key="String(item.提交编号)">
            <td>{{ item.提交编号 }}</td><td>{{ item.提交类型 }}</td><td>{{ item.站点编号 || '—' }}</td>
            <td>{{ item.新增条目数 }}</td><td>{{ item.可领取数 }}</td><td>{{ item.待核数 }}</td><td>{{ item.拦截数 }}</td>
            <td>{{ item.提交时间 }}</td>
          </tr>
        </tbody>
      </table>

      <h4>条目</h4>
      <table class="data-table">
        <thead><tr><th>条目编号</th><th>网址</th><th>站点</th><th>状态</th><th>批次</th><th>核验依据</th><th>说明</th></tr></thead>
        <tbody>
          <tr v-for="entry in trace.条目" :key="entry.条目编号">
            <td>{{ entry.条目编号 }}</td><td>{{ entry.来源URL }}</td><td>{{ entry.站点编号 }}</td>
            <td>{{ entry.条目状态 }}</td><td>{{ entry.批次编号 || '—' }}</td>
            <td>{{ entry.核验依据 || '—' }}</td><td>{{ entry.处理说明 || '—' }}</td>
          </tr>
        </tbody>
      </table>

      <h4>实际批次内容</h4>
      <table class="data-table">
        <thead><tr><th>批次编号</th><th>站点</th><th>状态</th><th>行数</th><th>落库地址</th><th>核验依据</th></tr></thead>
        <tbody>
          <tr v-for="batch in trace.批次" :key="batch.批次编号">
            <td>{{ batch.批次编号 }}</td><td>{{ batch.站点编号 }}（{{ batch.站点域名 }}）</td>
            <td>{{ batch.状态 }}</td><td>{{ batch.数据行数 }}</td>
            <td>
              <div v-for="content in batch.实际内容" :key="content.来源URL">
                {{ content.来源URL }} · {{ content.清册条目 }}
              </div>
              <span v-if="!batch.实际内容.length">{{ batch.说明 || '—' }}</span>
            </td>
            <td>
              <div v-for="content in batch.实际内容" :key="`${content.来源URL}-proof`">
                {{ content.核验依据 || '—' }}
              </div>
              <span v-if="!batch.实际内容.length">—</span>
            </td>
          </tr>
        </tbody>
      </table>

      <h4>历史批次（兼容无清册编号的旧数据）</h4>
      <table class="data-table">
        <thead><tr><th>批次编号</th><th>来源作业</th><th>站点</th><th>清册编号</th><th>状态</th></tr></thead>
        <tbody>
          <tr v-for="batch in trace.历史批次" :key="String(batch.批次编号)">
            <td>{{ batch.批次编号 }}</td><td>{{ batch.来源作业 }}</td>
            <td>{{ batch.站点编号 || '—' }}</td><td>{{ batch.清册编号 || '历史数据' }}</td>
            <td>{{ batch.状态 }}</td>
          </tr>
        </tbody>
      </table>
    </article>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import {
  claimLedger,
  listInventory,
  storeLedger,
  submitLedger,
  submitProof,
  submitSingleAddress,
  traceLedger,
} from '@/api/ledger-service'
import { listEntries } from '@/api/backend-service'
import type { LedgerClaimResult, LedgerTraceResult } from '@/data/types'

const ledgers = ref<Record<string, string | number | boolean>[]>([])
const activeLedger = ref('')
const trace = ref<LedgerTraceResult | null>(null)
const claim = ref<LedgerClaimResult | null>(null)
const busy = ref(false)
const errorMessage = ref('')
const notice = ref('')

const submitForm = reactive({
  ledger_id: '',
  name: '值班混合来源清册',
  site_id: 'SITE-A',
  single_url: '',
  mixed: '',
})

const proofForm = reactive({ site_id: 'SITE-B', proof: '' })

const waiting = computed(() => claim.value?.等待核验 ?? [])
const siteBatches = computed(() => claim.value?.站点批次 ?? {})
const executableText = computed(() => {
  if (!claim.value) return '—'
  if (!claim.value.可执行) return '本次无可执行站点'
  return claim.value.可执行站点.join('、')
})
const claimSummary = computed(() => claim.value?.说明 ?? '尚未领取；点「领取任务」只消费已有资格的地址。')

function fail(error: unknown) {
  errorMessage.value = error instanceof Error ? error.message : '清册操作失败'
}

async function reloadAll() {
  errorMessage.value = ''
  try {
    const payload = await listEntries('source_ledger')
    ledgers.value = payload.items
    if (!activeLedger.value && ledgers.value.length) {
      activeLedger.value = String(ledgers.value[0].清册编号)
    }
    if (activeLedger.value) await loadTrace(activeLedger.value)
  } catch (error) {
    fail(error)
  }
}

async function loadTrace(ledgerId: string) {
  try {
    trace.value = await traceLedger(ledgerId)
    await listInventory(ledgerId)
  } catch (error) {
    fail(error)
  }
}

async function selectLedger(ledgerId: string) {
  activeLedger.value = ledgerId
  submitForm.ledger_id = ledgerId
  await loadTrace(ledgerId)
}

function parseMixed(text: string): { url: string; site_id: string }[] {
  return text
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const parts = line.split(/[\s,，]+/).filter(Boolean)
      if (parts.length >= 2) return { site_id: parts[0], url: parts.slice(1).join('') }
      return { site_id: '', url: parts[0] }
    })
}

async function afterSubmit(result: { 清册编号: string; message: string }) {
  notice.value = result.message
  activeLedger.value = result.清册编号
  submitForm.ledger_id = result.清册编号
  await reloadAll()
}

async function doSubmitSingle() {
  if (!submitForm.site_id || !submitForm.single_url) {
    errorMessage.value = '单独提交需要站点编号与网址'
    return
  }
  busy.value = true
  errorMessage.value = ''
  try {
    const result = await submitSingleAddress({
      ledger_id: submitForm.ledger_id || undefined,
      name: submitForm.name,
      site_id: submitForm.site_id,
      urls: [submitForm.single_url.trim()],
    })
    await afterSubmit(result)
  } catch (error) {
    fail(error)
  } finally {
    busy.value = false
  }
}

async function doSubmitMixed() {
  const entries = parseMixed(submitForm.mixed)
  if (!entries.length) {
    errorMessage.value = '混合提交至少需要一行「站点编号 网址」'
    return
  }
  busy.value = true
  errorMessage.value = ''
  try {
    const result = await submitLedger({
      ledger_id: submitForm.ledger_id || undefined,
      name: submitForm.name,
      entries,
      origin: '原始提交',
    })
    await afterSubmit(result)
  } catch (error) {
    fail(error)
  } finally {
    busy.value = false
  }
}

async function doProof() {
  if (!activeLedger.value || !proofForm.site_id || !proofForm.proof) {
    errorMessage.value = '补交证明需要站点编号与核验依据'
    return
  }
  busy.value = true
  errorMessage.value = ''
  try {
    const result = (await submitProof({
      ledger_id: activeLedger.value,
      site_id: proofForm.site_id,
      proof: proofForm.proof,
    })) as { message?: string }
    notice.value = String(result.message ?? '核验证明已记录')
    await reloadAll()
  } catch (error) {
    fail(error)
  } finally {
    busy.value = false
  }
}

async function doClaim(ledgerId: string) {
  busy.value = true
  errorMessage.value = ''
  try {
    claim.value = await claimLedger(ledgerId)
    notice.value = claim.value.说明
    activeLedger.value = ledgerId
    submitForm.ledger_id = ledgerId
    await reloadAll()
  } catch (error) {
    fail(error)
  } finally {
    busy.value = false
  }
}

async function doStore(ledgerId: string) {
  busy.value = true
  errorMessage.value = ''
  try {
    const result = (await storeLedger(ledgerId)) as { message?: string }
    notice.value = String(result.message ?? '已落库')
    await reloadAll()
  } catch (error) {
    fail(error)
  } finally {
    busy.value = false
  }
}

onMounted(reloadAll)
</script>
