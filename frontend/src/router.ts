import { createRouter, createWebHistory } from 'vue-router'

import ChatView from './views/ChatView.vue'
import KnowledgeView from './views/KnowledgeView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/chat' },
    { path: '/chat', name: 'chat', component: ChatView },
    { path: '/knowledge', name: 'knowledge', component: KnowledgeView },
  ],
})

export default router
