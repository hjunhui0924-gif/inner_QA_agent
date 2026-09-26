import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'home',
      meta: { layout: 'landing' },
      component: () => import('./views/HomeView.vue'),
    },
    {
      path: '/chat',
      name: 'chat',
      meta: { layout: 'chat' },
      component: () => import('./views/ChatView.vue'),
    },
    {
      path: '/knowledge',
      name: 'knowledge',
      meta: { layout: 'management' },
      component: () => import('./views/KnowledgeView.vue'),
    },
  ],
})

export default router
