<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Document, Plus, Delete, ChatDotRound, Search, Refresh } from '@element-plus/icons-vue'

import { useWorkspace } from '../composables/useWorkspace'
import type { SessionSummary } from '../types/api'
import { filterSessions } from '../utils/sessions'

const props = defineProps<{ open: boolean }>()

const emit = defineEmits<{ close: [] }>()
const {
  sessionId,
  sessions,
  backendOnline,
  loadingSessions,
  sessionsError,
  loadSessions,
  loadingHistory,
  historyError,
  sending,
  sessionTitlePending,
  newSession,
  openSession,
  retryOpenSession,
  removeSession,
} = useWorkspace()
const pendingDelete = ref<SessionSummary | null>(null)
const deleting = ref(false)
const sessionQuery = ref('')
const sessionSearchInput = ref<HTMLInputElement | null>(null)
const startChatButton = ref<HTMLButtonElement | null>(null)
const deleteCancelButton = ref<HTMLButtonElement | null>(null)
const deleteTrigger = ref<HTMLButtonElement | null>(null)
const deleteDialog = ref<HTMLElement | null>(null)
const mobileViewport = ref(false)

const filteredSessions = computed(() => filterSessions(sessions.value, sessionQuery.value))

function startSession() {
  newSession()
  emit('close')
}

async function selectSession(targetSessionId: string) {
  const opened = await openSession(targetSessionId)
  if (opened) emit('close')
}

async function retryHistory(): Promise<void> {
  await retryOpenSession()
}

function requestDelete(session: SessionSummary, event: MouseEvent): void {
  pendingDelete.value = session
  deleteTrigger.value = event.currentTarget as HTMLButtonElement
}

function finishCloseDeleteDialog(force: boolean): void {
  if (deleting.value && !force) return
  pendingDelete.value = null
  void nextTick(() => {
    if (deleteTrigger.value?.isConnected) {
      deleteTrigger.value.focus()
    } else {
      sessionSearchInput.value?.focus()
    }
  })
}

function closeDeleteDialog(): void {
  finishCloseDeleteDialog(false)
}

function closeDeleteDialogAfterDelete(): void {
  finishCloseDeleteDialog(true)
}

async function confirmDelete() {
  if (!pendingDelete.value) return
  deleting.value = true
  try {
    await removeSession(pendingDelete.value.session_id)
    closeDeleteDialogAfterDelete()
  } catch {
    // The workspace already presents a persistent error toast.
  } finally {
    deleting.value = false
  }
}

function handleKeydown(event: KeyboardEvent): void {
  if (pendingDelete.value) {
    if (event.key === 'Escape') {
      event.preventDefault()
      closeDeleteDialog()
      return
    }
    if (event.key === 'Tab') {
      trapDeleteDialogFocus(event)
    }
    return
  }
  if (event.key !== 'Escape') return
  if (props.open) {
    event.preventDefault()
    emit('close')
  }
}

function trapDeleteDialogFocus(event: KeyboardEvent): void {
  const focusable = deleteDialog.value?.querySelectorAll<HTMLElement>(
    'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
  )
  if (!focusable?.length) return
  const first = focusable[0]
  const last = focusable[focusable.length - 1]
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first.focus()
  }
}

function updateMobileViewport(): void {
  mobileViewport.value = window.innerWidth <= 760
}

watch(
  () => props.open,
  (open) => {
    if (open) void nextTick(() => startChatButton.value?.focus())
  },
)

watch(
  pendingDelete,
  (session) => {
    if (session) void nextTick(() => deleteCancelButton.value?.focus())
  },
)

onMounted(() => {
  updateMobileViewport()
  window.addEventListener('resize', updateMobileViewport)
  window.addEventListener('keydown', handleKeydown)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', updateMobileViewport)
  window.removeEventListener('keydown', handleKeydown)
})
</script>

<template>
  <div class="sidebar-scrim" :class="{ visible: open }" @click="emit('close')" />
  <aside
    class="app-sidebar"
    :class="{ open }"
    aria-label="主导航"
    :aria-hidden="mobileViewport && !open ? 'true' : undefined"
    :inert="mobileViewport && !open ? true : undefined"
  >
    <header class="wordmark">
      <div class="brand-new-chat">
        <img src="/knowledge-assistant.png" alt="" class="brand-icon" />
        <span>内部知识助手</span>
      </div>
    </header>

    <button ref="startChatButton" class="start-chat-button" type="button" :disabled="sending" @click="startSession">
      <el-icon><Plus /></el-icon><span>开启新对话</span>
    </button>

    <nav class="primary-nav" aria-label="工作区">
      <RouterLink to="/chat"><el-icon><ChatDotRound /></el-icon><span>对话</span></RouterLink>
      <RouterLink to="/knowledge"><el-icon><Document /></el-icon><span>知识库</span></RouterLink>
    </nav>

    <section class="session-section" aria-labelledby="session-heading">
      <div class="section-heading">
        <h2 id="session-heading">会话记录</h2>
      </div>

      <label class="session-search">
        <span class="sr-only">搜索会话</span>
        <el-icon aria-hidden="true"><Search /></el-icon>
        <input ref="sessionSearchInput" v-model="sessionQuery" type="search" placeholder="搜索会话" />
      </label>

      <div v-if="loadingSessions" class="session-empty">正在读取会话…</div>
      <div v-else-if="sessionTitlePending" class="session-progress" role="status" aria-live="polite">
        正在生成当前会话标题…
      </div>
      <div v-else-if="sessionsError" class="session-error" role="alert">
        <span>{{ sessionsError }}</span>
        <button type="button" :disabled="loadingSessions" @click="loadSessions">
          <el-icon><Refresh /></el-icon> 重试
        </button>
      </div>
      <div v-else-if="historyError" class="session-error" role="alert">
        <span>{{ historyError }}</span>
        <button type="button" :disabled="loadingHistory" @click="retryHistory">
          <el-icon><Refresh /></el-icon> 重试加载
        </button>
      </div>
      <div v-else-if="sessions.length === 0" class="session-empty">
        暂无历史会话，从一次提问开始。
      </div>
      <div v-else-if="filteredSessions.length === 0" class="session-empty">
        没有匹配的会话。
      </div>
      <div v-else class="session-list">
        <div
          v-for="session in filteredSessions"
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
            <span v-if="sessionTitlePending && session.session_id === sessionId" class="session-title-status">
              正在生成标题…
            </span>
          </button>
          <button
            class="session-delete"
            type="button"
            :disabled="sending || loadingHistory"
            :aria-label="`删除会话：${session.title || '新会话'}`"
            @click="requestDelete(session, $event)"
          >
            <el-icon><Delete /></el-icon>
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

  <div v-if="pendingDelete" class="modal-scrim" role="presentation" @click.self="closeDeleteDialog">
    <section ref="deleteDialog" class="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="delete-title" aria-describedby="delete-description">
      <p class="eyebrow">DELETE SESSION</p>
      <h2 id="delete-title">彻底删除这个会话？</h2>
      <p>
        <span id="delete-description">“{{ pendingDelete.title || '新会话' }}”的聊天记录、摘要和 Agent 状态都会被删除，且无法恢复。</span>
      </p>
      <div class="dialog-actions">
        <button ref="deleteCancelButton" class="secondary-button" type="button" :disabled="deleting" @click="closeDeleteDialog">
          取消
        </button>
        <button class="danger-button" type="button" :disabled="deleting" @click="confirmDelete">
          {{ deleting ? '正在删除…' : '确认删除' }}
        </button>
      </div>
    </section>
  </div>
</template>
