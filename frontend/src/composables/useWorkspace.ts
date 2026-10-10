import { computed, ref } from 'vue'

import { fetchHealth } from '../services/api'
import type {
  Citation,
  UiMessage,
} from '../types/api'
import {
} from '../utils/performance'
import { createNotificationState } from '../state/notificationState'
import { createSessionState } from '../state/sessionState'
import { createChatState } from '../state/chatState'
import { createKnowledgeState } from '../state/knowledgeState'
import { createContextUsageState } from '../state/contextUsageState'
import { createContextActions } from '../state/contextActions'
import { createKnowledgeActions } from '../state/knowledgeActions'
import { createSessionActions } from '../state/sessionActions'
import { createChatActions } from '../state/chatActions'

const userId = 'user_001'

function generateUuid(): string {
  if (
    typeof crypto !== 'undefined' &&
    typeof crypto.randomUUID === 'function'
  ) {
    return crypto.randomUUID()
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`
}

const sessionState = createSessionState(generateUuid)
const chatState = createChatState()
const knowledgeState = createKnowledgeState()
const contextState = createContextUsageState()
const {
  sessionId, sessions, viewCache, currentView, draft, loadingSessions,
  sessionsError, loadingHistory, historyError, historyErrorSessionId,
  sessionTitlePending, chatVisible,
} = sessionState
const {
  messages, agentSteps, activeCitations, backendOnline, sending,
  sendingStartedAt, chatMode, webSearchEnabled, modelProvider,
} = chatState
const {
  knowledgeRecords, loadingKnowledge, knowledgeError, uploading,
  uploadForm, uploadTask,
} = knowledgeState
const {
  contextUsage, contextUsageLoading, contextUsageStale,
} = contextState
const initialized = ref(false)
let initialization: Promise<void> | null = null
let chatInitialization: Promise<void> | null = null
let knowledgeInitialization: Promise<void> | null = null

const activeSession = computed(
  () =>
    sessions.value.find((item) => item.session_id === sessionId.value) ?? null,
)
const activeSessionTitle = computed(
  () => activeSession.value?.title || '新会话',
)

function createId(prefix: string): string {
  return `${prefix}-${generateUuid()}`
}

const {
  toasts,
  notify,
  dismissToast,
} = createNotificationState(() => createId('toast'))

function readableError(error: unknown): string {
  if (error instanceof DOMException && error.name === 'AbortError')
    return '请求已取消。'
  const status =
    error && typeof error === 'object' && 'status' in error ? error.status : 0
  if (status === 401) return '身份验证失败，请联系管理员。'
  if (status === 403) return '当前身份无权执行此操作。'
  if (typeof status === 'number' && status >= 500)
    return '服务暂时不可用，请稍后重试。'
  return '无法连接服务，请检查网络后重试。'
}

function sessionOperationFailureDetail(): string {
  return '会话操作未完成，请稍后重试。'
}

const contextActions = createContextActions({
  userId,
  sessionId,
  state: contextState,
})
const knowledgeActions = createKnowledgeActions({
  state: knowledgeState,
  readableError,
  notify,
})
const { refreshContextUsage } = contextActions
const { initialized: knowledgeInitialized, loadKnowledge, uploadKnowledge } = knowledgeActions
const sessionActions = createSessionActions({
  userId,
  state: sessionState,
  chat: chatState,
  context: contextState,
  generateId: generateUuid,
  notify,
  refreshContextUsage,
  sessionOperationFailureDetail,
  sending,
})
const {
  initialized: chatInitialized,
  setChatVisible,
  loadSessions,
  refreshTitleLater,
  cancelTitleRefresh,
  invalidateSessions,
  newSession,
  openSession,
  retryOpenSession,
  removeSession,
  rememberSessionMode,
} = sessionActions
const chatActions = createChatActions({
  userId,
  generateId: generateUuid,
  sessionId,
  sessions,
  messages,
  agentSteps,
  activeCitations,
  backendOnline,
  sending,
  sendingStartedAt,
  chatMode,
  webSearchEnabled,
  modelProvider,
  sessionTitlePending,
  chatVisible,
  notify,
  readableError,
  isAbortError: (error) => error instanceof DOMException && error.name === 'AbortError',
  cancelTitleRefresh,
  rememberSessionMode,
  loadSessions,
  refreshContextUsage,
  refreshTitleLater,
  invalidateSessions,
})
const { sendMessage, cancelMessage, retryMessage } = chatActions

async function checkHealth(): Promise<void> {
  if (initialized.value && backendOnline.value) return
  if (!initialization)
    initialization = (async () => {
      try {
        const health = await fetchHealth()
        if (health.model_provider === 'qwen' || health.model_provider === 'deepseek') {
          modelProvider.value = health.model_provider
        }
        backendOnline.value = true
        initialized.value = true
      } catch (error) {
        backendOnline.value = false
        notify('error', '无法连接后端', readableError(error))
      } finally {
        initialization = null
      }
    })()
  await initialization
}

async function initialize(
  target: 'chat' | 'knowledge' = 'chat',
): Promise<void> {
  const health = checkHealth()
  if (target === 'chat' && !chatInitialized.value) {
    if (!chatInitialization)
      chatInitialization = loadSessions().finally(() => {
        chatInitialization = null
      })
    await chatInitialization
    void refreshContextUsage()
  }
  if (target === 'knowledge' && !knowledgeInitialized.value) {
    if (!knowledgeInitialization)
      knowledgeInitialization = loadKnowledge().finally(() => {
        knowledgeInitialization = null
      })
    await knowledgeInitialization
  }
  await health
}

function showCitations(citations: Citation[]): void {
  activeCitations.value = citations
}

export function useWorkspace() {
  return {
    userId,
    draft,
    viewCache,
    currentView,
    setChatVisible,
    sessionId,
    sessions,
    messages,
    knowledgeRecords,
    agentSteps,
    activeCitations,
    activeSessionTitle,
    backendOnline,
    loadingSessions,
    sessionsError,
    loadingHistory,
    historyError,
    historyErrorSessionId,
    loadingKnowledge,
    sending,
    sendingStartedAt,
    chatMode,
    webSearchEnabled,
    modelProvider,
    uploading,
    uploadTask,
    uploadForm,
    knowledgeError,
    sessionTitlePending,
    contextUsage,
    contextUsageLoading,
    contextUsageStale,
    toasts,
    initialize,
    loadSessions,
    loadKnowledge,
    newSession,
    openSession,
    retryOpenSession,
    removeSession,
    sendMessage,
    cancelMessage,
    retryMessage,
    showCitations,
    uploadKnowledge,
    dismissToast,
  }
}
