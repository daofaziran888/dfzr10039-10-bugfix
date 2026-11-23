import { createRouter, createWebHistory } from 'vue-router'

import LedgerView from '@/views/LedgerView.vue'
import Overview from '@/views/Overview.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    // 来源清册有自己的跨模块工作台，必须排在通用模块路由之前。
    { path: '/source_ledger', name: 'source_ledger', component: LedgerView },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
