<script setup lang="ts">
import { ref } from 'vue'

import { useWorkspace } from '../composables/useWorkspace'
import type { SessionSummary } from '../types/api'

defineProps<{ open: boolean }>()

const emit = defineEmits<{ close: [] }>()
const {
  sessionId,
  sessions,
  backendOnline,
  loadingSessions,
  loadingHistory,
  sending,
  newSession,
  openSession,
  removeSession,
} = useWorkspace()
const pendingDelete = ref<SessionSummary | null>(null)
const deleting = ref(false)

function startSession() {
  newSession()
  emit('close')
}

async function selectSession(targetSessionId: string) {
  await openSession(targetSessionId)
  emit('close')
}

async function confirmDelete() {
  if (!pendingDelete.value) return
  deleting.value = true
  try {
    await removeSession(pendingDelete.value.session_id)
    pendingDelete.value = null
  } catch {
    // The workspace already presents a persistent error toast.
  } finally {
    deleting.value = false
  }
}
</script>

<template>
  <div class="sidebar-scrim" :class="{ visible: open }" @click="emit('close')" />
  <aside class="app-sidebar" :class="{ open }" aria-label="主导航">
    <header class="wordmark">
      <div>
        <p class="eyebrow">ENTERPRISE KNOWLEDGE</p>
        <strong>企业内部知识助手</strong>
      </div>
        <span class="live-tag">LIVE</span>
    </header>

    <nav class="primary-nav" aria-label="工作区">
      <RouterLink to="/chat">
        <span class="nav-index">01</span>
        <span>知识问答</span>
      </RouterLink>
      <RouterLink to="/knowledge">
        <span class="nav-index">02</span>
        <span>知识库</span>
      </RouterLink>
    </nav>

    <section class="session-section" aria-labelledby="session-heading">
      <div class="section-heading">
        <h2 id="session-heading">会话记录</h2>
        <button class="compact-button" type="button" :disabled="sending" @click="startSession">
          新建
        </button>
      </div>

      <div v-if="loadingSessions" class="session-empty">正在读取会话…</div>
      <div v-else-if="sessions.length === 0" class="session-empty">
        暂无历史会话，从一次提问开始。
      </div>
      <div v-else class="session-list">
        <div
          v-for="session in sessions"
          :key="session.session_id"
          class="session-item"
          :class="{ active: session.session_id === sessionId }"
        >
          <button
            class="session-row"
            type="button"
            :disabled="sending || loadingHistory"
            @click="selectSession(session.session_id)"
          >
            <span class="session-title">{{ session.title || '新会话' }}</span>
            <span class="session-meta">{{ session.last_message || '暂无消息' }}</span>
          </button>
          <button
            class="session-delete"
            type="button"
            :disabled="sending || loadingHistory"
            :aria-label="`删除会话：${session.title || '新会话'}`"
            @click="pendingDelete = session"
          >
            删除
          </button>
        </div>
      </div>
    </section>

    <footer class="sidebar-footer">
      <span class="status-dot" :class="{ online: backendOnline }" aria-hidden="true" />
      <div>
        <strong>{{ backendOnline ? '知识服务在线' : '知识服务离线' }}</strong>
        <span>{{ backendOnline ? 'FastAPI 已连接' : '请检查后端服务' }}</span>
      </div>
    </footer>
  </aside>

  <div v-if="pendingDelete" class="modal-scrim" role="presentation">
    <section class="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="delete-title">
      <p class="eyebrow">DELETE SESSION</p>
      <h2 id="delete-title">彻底删除这个会话？</h2>
      <p>
        “{{ pendingDelete.title || '新会话' }}”的聊天记录、摘要和 Agent 状态都会被删除，且无法恢复。
      </p>
      <div class="dialog-actions">
        <button class="secondary-button" type="button" :disabled="deleting" @click="pendingDelete = null">
          取消
        </button>
        <button class="danger-button" type="button" :disabled="deleting" @click="confirmDelete">
          {{ deleting ? '正在删除…' : '确认删除' }}
        </button>
      </div>
    </section>
  </div>
</template>
