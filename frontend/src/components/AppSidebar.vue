<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Document, Plus, Delete, ChatDotRound, Search, Refresh } from '@element-plus/icons-vue'

import { useWorkspace } from '../composables/useWorkspace'
import type { SessionSummary } from '../types/api'
import { filterSessions } from '../utils/sessions'
import { registerOverlay } from '../utils/overlayStack'

const props = defineProps<{ open: boolean; blocked?: boolean }>()

const emit = defineEmits<{
  close: []
  'modal-change': [open: boolean]
}>()
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
const sidebar = ref<HTMLElement | null>(null)
const mobileViewport = ref(false)
let sidebarOverlay: ReturnType<typeof registerOverlay> | null = null
let deleteOverlay: ReturnType<typeof registerOverlay> | null = null

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

function handleSidebarKeydown(event: KeyboardEvent): void {
  if (mobileViewport.value && event.key === 'Tab') {
    trapSidebarFocus(event)
    return
  }
  if (event.key !== 'Escape') return
  event.preventDefault()
  emit('close')
}

function handleDeleteKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    event.preventDefault()
    closeDeleteDialog()
    return
  }
  if (event.key === 'Tab') trapDeleteDialogFocus(event)
}

function trapSidebarFocus(event: KeyboardEvent): void {
  const focusable = sidebar.value?.querySelectorAll<HTMLElement>(
    'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
  )
  if (!focusable?.length) return
  const first = focusable[0]
  const last = focusable[focusable.length - 1]
  if (!sidebar.value?.contains(document.activeElement)) {
    event.preventDefault()
    first.focus()
    return
  }
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first.focus()
  }
}

function trapDeleteDialogFocus(event: KeyboardEvent): void {
  const focusable = deleteDialog.value?.querySelectorAll<HTMLElement>(
    'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
  )
  if (!focusable?.length) {
    event.preventDefault()
    deleteDialog.value?.focus()
    return
  }
  const first = focusable[0]
  const last = focusable[focusable.length - 1]
  if (!deleteDialog.value?.contains(document.activeElement)) {
    event.preventDefault()
    first.focus()
    return
  }
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
  sidebarOverlay?.setOpen(props.open)
}

watch(
  () => props.open,
  (open) => {
    sidebarOverlay?.setOpen(open)
    if (open) void nextTick(() => startChatButton.value?.focus())
  },
)

watch(pendingDelete, (session) => {
  deleteOverlay?.setOpen(Boolean(session))
  emit('modal-change', Boolean(session))
  if (session) void nextTick(() => deleteCancelButton.value?.focus())
})

onMounted(() => {
  updateMobileViewport()
  window.addEventListener('resize', updateMobileViewport)
  sidebarOverlay = registerOverlay(handleSidebarKeydown)
  deleteOverlay = registerOverlay(handleDeleteKeydown, { modal: true })
  sidebarOverlay.setOpen(props.open)
  deleteOverlay.setOpen(Boolean(pendingDelete.value))
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', updateMobileViewport)
  sidebarOverlay?.unregister()
  deleteOverlay?.unregister()
  sidebarOverlay = null
  deleteOverlay = null
})
</script>

<template>
  <div class="sidebar-scrim" :class="{ visible: open }" @click="emit('close')" />
  <aside
    ref="sidebar"
    class="app-sidebar"
    :class="{ open }"
    aria-label="主导航"
    :aria-hidden="(mobileViewport && !open) || pendingDelete || blocked ? 'true' : undefined"
    :inert="(mobileViewport && !open) || pendingDelete || blocked ? true : undefined"
  >
    <header class="wordmark">
      <div class="brand-new-chat">
        <img src="/knowledge-assistant.png" alt="" width="27" height="27" class="brand-icon" />
        <div><span>内部知识助手</span><small>团队知识工作台</small></div>
      </div>
    </header>

    <button
      ref="startChatButton"
      class="start-chat-button"
      type="button"
      :disabled="sending"
      @click="startSession"
    >
      <el-icon aria-hidden="true"><Plus /></el-icon><span>开启新对话</span>
    </button>

    <nav class="primary-nav" aria-label="工作区">
      <RouterLink to="/chat"
        ><el-icon aria-hidden="true"><ChatDotRound /></el-icon><span>对话</span></RouterLink
      >
      <RouterLink to="/knowledge"
        ><el-icon aria-hidden="true"><Document /></el-icon><span>知识库</span></RouterLink
      >
    </nav>

    <section class="session-section" aria-labelledby="session-heading">
      <div class="section-heading">
        <h2 id="session-heading">会话记录</h2>
      </div>

      <label class="session-search">
        <span class="sr-only">搜索会话</span>
        <el-icon aria-hidden="true"><Search /></el-icon>
        <input
          ref="sessionSearchInput"
          v-model="sessionQuery"
          name="session-search"
          autocomplete="off"
          spellcheck="false"
          type="search"
          placeholder="搜索会话"
        />
      </label>

      <div v-if="loadingSessions" class="session-empty">正在读取会话…</div>
      <div v-else-if="sessionsError" class="session-error" role="alert">
        <span>{{ sessionsError }}</span>
        <button type="button" :disabled="loadingSessions" @click="loadSessions">
          <el-icon aria-hidden="true"><Refresh /></el-icon> 重试
        </button>
      </div>
      <div v-else-if="historyError" class="session-error" role="alert">
        <span>{{ historyError }}</span>
        <button type="button" :disabled="loadingHistory" @click="retryHistory">
          <el-icon aria-hidden="true"><Refresh /></el-icon> 重试加载
        </button>
      </div>
      <div
        v-else-if="sessionTitlePending"
        class="session-progress"
        role="status"
        aria-live="polite"
      >
        正在生成当前会话标题…
      </div>
      <div v-else-if="sessions.length === 0" class="session-empty">
        暂无历史会话，从一次提问开始。
      </div>
      <div v-else-if="filteredSessions.length === 0" class="session-empty">没有匹配的会话。</div>
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
            :aria-current="session.session_id === sessionId ? 'page' : undefined"
            @click="selectSession(session.session_id)"
          >
            <span class="session-title">{{ session.title || '新会话' }}</span>
            <span
              v-if="sessionTitlePending && session.session_id === sessionId"
              class="session-title-status"
            >
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
            <el-icon aria-hidden="true"><Delete /></el-icon>
          </button>
        </div>
      </div>
    </section>

    <footer class="sidebar-footer">
      <span class="status-dot" :class="{ online: backendOnline }" aria-hidden="true" />
      <div>
        <strong>{{ backendOnline ? '知识服务在线' : '知识服务离线' }}</strong>
        <span>{{ backendOnline ? '随时查找工作中的答案' : '请检查网络或联系管理员' }}</span>
      </div>
    </footer>
  </aside>

  <div v-if="pendingDelete" class="modal-scrim" role="presentation" @click.self="closeDeleteDialog">
    <section
      ref="deleteDialog"
      class="confirm-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="delete-title"
      aria-describedby="delete-description"
      tabindex="-1"
    >
      <p class="eyebrow">会话管理</p>
      <h2 id="delete-title">彻底删除这个会话？</h2>
      <p>
        <span id="delete-description"
          >“{{ pendingDelete.title || '新会话' }}”的聊天记录和上下文都会被删除，且无法恢复。</span
        >
      </p>
      <div class="dialog-actions">
        <button
          ref="deleteCancelButton"
          class="secondary-button"
          type="button"
          :disabled="deleting"
          @click="closeDeleteDialog"
        >
          取消
        </button>
        <button class="danger-button" type="button" :disabled="deleting" @click="confirmDelete">
          {{ deleting ? '正在删除…' : '确认删除' }}
        </button>
      </div>
    </section>
  </div>
</template>
