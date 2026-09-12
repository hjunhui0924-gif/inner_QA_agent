import { computed, ref } from 'vue'

import {
  deleteSession as deleteSessionRequest,
  fetchHealth,
  fetchHistory,
  fetchKnowledgeRecords,
  fetchSessions,
  streamChat,
  uploadKnowledge as uploadKnowledgeRequest,
} from '../services/api'
import type {
  AgentStep,
  Citation,
  KnowledgeRecord,
  SessionSummary,
  StreamEvent,
  ToastMessage,
  UiMessage,
  UploadResponse,
  ChatMode,
  KnowledgeUploadMetadata,
} from '../types/api'
import { readableKnowledgeUploadError } from '../utils/knowledge'
import { nextSessionAfterDelete } from '../utils/sessions'
import {
  createStreamState,
  reduceStreamEvent,
  type StreamUiState,
} from '../utils/streamState'

const userId = 'user_001'

function generateUuid(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`
}

const sessionId = ref<string>(generateUuid())
const sessions = ref<SessionSummary[]>([])
const messages = ref<UiMessage[]>([])
const knowledgeRecords = ref<KnowledgeRecord[]>([])
const agentSteps = ref<AgentStep[]>([])
const activeCitations = ref<Citation[]>([])
const backendOnline = ref(false)
const initialized = ref(false)
const loadingSessions = ref(false)
const sessionsError = ref<string | null>(null)
const loadingHistory = ref(false)
const historyError = ref<string | null>(null)
const historyErrorSessionId = ref<string | null>(null)
const loadingKnowledge = ref(false)
const sending = ref(false)
const chatMode = ref<ChatMode>('knowledge')
const webSearchEnabled = ref(false)
const uploading = ref(false)
const sessionTitlePending = ref(false)
const toasts = ref<ToastMessage[]>([])
let activeController: AbortController | null = null
let historyRequestGeneration = 0
const sessionModeStorageKey = 'enterprise-assistant-session-modes'

function readSessionModes(): Record<string, ChatMode> {
  try {
    return JSON.parse(localStorage.getItem(sessionModeStorageKey) || '{}') as Record<string, ChatMode>
  } catch {
    return {}
  }
}

function rememberSessionMode(targetSessionId: string, mode: ChatMode): void {
  localStorage.setItem(
    sessionModeStorageKey,
    JSON.stringify({ ...readSessionModes(), [targetSessionId]: mode }),
  )
}

const activeSession = computed(
  () => sessions.value.find((item) => item.session_id === sessionId.value) ?? null,
)
const activeSessionTitle = computed(() => activeSession.value?.title || '新会话')

function createId(prefix: string): string {
  return `${prefix}-${generateUuid()}`
}

function notify(
  tone: ToastMessage['tone'],
  title: string,
  detail?: string,
): void {
  const toast: ToastMessage = { id: createId('toast'), tone, title, detail }
  toasts.value.push(toast)
  window.setTimeout(() => dismissToast(toast.id), 4200)
}

function dismissToast(id: string): void {
  toasts.value = toasts.value.filter((toast) => toast.id !== id)
}

function readableError(error: unknown): string {
  if (error instanceof DOMException && error.name === 'AbortError') return '请求已取消。'
  return error instanceof Error ? error.message : '发生未知错误。'
}

function chatFailureDetail(): string {
  return '请求未完成，候选内容已丢弃。请稍后重新发送。'
}

function sessionOperationFailureDetail(): string {
  return '会话操作未完成，请稍后重试。'
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

function uiStateForAnswer(answerState: UiMessage['answerState']): UiMessage['state'] {
  if (answerState === 'error') return 'error'
  if (answerState === 'cancelled') return 'cancelled'
  if (answerState === 'complete' || answerState === 'fallback') return 'complete'
  return 'streaming'
}

function syncAssistantMessage(
  assistant: UiMessage,
  streamState: StreamUiState,
): void {
  assistant.answerState = streamState.answerState
  assistant.state = uiStateForAnswer(streamState.answerState)
  assistant.failureType = streamState.failureType
  assistant.failureStage = streamState.failureStage
  assistant.failureReason = streamState.failureReason
  assistant.traceId = streamState.traceId

  if (streamState.resultReceived) {
    assistant.content = streamState.content
    assistant.citations = streamState.citations
    activeCitations.value = streamState.citations
  }
}

async function loadSessions(): Promise<void> {
  loadingSessions.value = true
  sessionsError.value = null
  try {
    sessions.value = await fetchSessions(userId)
  } catch (error) {
    sessionsError.value = '会话列表暂时无法加载，请重试。'
    notify('error', '会话列表加载失败', sessionsError.value)
  } finally {
    loadingSessions.value = false
  }
}

async function loadKnowledge(): Promise<void> {
  loadingKnowledge.value = true
  try {
    knowledgeRecords.value = await fetchKnowledgeRecords()
  } catch (error) {
    notify('error', '知识库加载失败', readableError(error))
  } finally {
    loadingKnowledge.value = false
  }
}

async function initialize(): Promise<void> {
  if (initialized.value) return
  initialized.value = true
  try {
    await fetchHealth()
    backendOnline.value = true
    await Promise.all([loadSessions(), loadKnowledge()])
  } catch (error) {
    backendOnline.value = false
    notify('error', '无法连接后端', readableError(error))
  }
}

function resetConversation(preserveHistoryError = false): void {
  historyRequestGeneration += 1
  loadingHistory.value = false
  sessionId.value = generateUuid()
  messages.value = []
  agentSteps.value = []
  activeCitations.value = []
  sessionTitlePending.value = false
  if (!preserveHistoryError) {
    historyError.value = null
    historyErrorSessionId.value = null
  }
  chatMode.value = 'knowledge'
  webSearchEnabled.value = false
}

function newSession(): void {
  if (sending.value) return
  resetConversation()
}

async function openSession(targetSessionId: string): Promise<boolean> {
  if (sending.value || loadingHistory.value) return false
  if (targetSessionId === sessionId.value) {
    historyError.value = null
    historyErrorSessionId.value = null
    return true
  }
  const requestGeneration = ++historyRequestGeneration
  loadingHistory.value = true
  historyError.value = null
  historyErrorSessionId.value = null
  try {
    const history = await fetchHistory(userId, targetSessionId)
    if (requestGeneration !== historyRequestGeneration) return false
    sessionId.value = targetSessionId
    chatMode.value = readSessionModes()[targetSessionId] ?? 'knowledge'
    webSearchEnabled.value = false
    messages.value = history.map((message) => ({
      ...message,
      id: message.message_id || createId(message.role),
      citations: message.citations ?? [],
      state: 'complete',
      answerState: message.failure_type && message.failure_type !== 'none'
        ? 'fallback'
        : 'complete',
      failureType: message.failure_type ?? null,
      failureStage: message.failure_stage ?? null,
      failureReason: message.failure_reason ?? null,
      traceId: message.trace_id ?? null,
    }))
    activeCitations.value =
      [...messages.value].reverse().find((message) => message.citations?.length)?.citations ?? []
    agentSteps.value = []
    return true
  } catch (error) {
    if (requestGeneration !== historyRequestGeneration) return false
    historyError.value = '会话内容暂时无法加载，请重试。'
    historyErrorSessionId.value = targetSessionId
    notify('error', '会话加载失败', historyError.value)
    return false
  } finally {
    if (requestGeneration === historyRequestGeneration) loadingHistory.value = false
  }
}

async function retryOpenSession(): Promise<boolean> {
  if (!historyErrorSessionId.value) return false
  return openSession(historyErrorSessionId.value)
}

async function removeSession(targetSessionId: string): Promise<void> {
  const sessionsBeforeDelete = [...sessions.value]
  const wasActive = sessionId.value === targetSessionId
  historyRequestGeneration += 1
  loadingHistory.value = false
  historyError.value = null
  historyErrorSessionId.value = null
  try {
    await deleteSessionRequest(userId, targetSessionId)
    sessions.value = sessions.value.filter((item) => item.session_id !== targetSessionId)
    if (wasActive) {
      const nextSessionId = nextSessionAfterDelete(sessionsBeforeDelete, targetSessionId)
      if (nextSessionId) {
        const opened = await openSession(nextSessionId)
        if (!opened) {
          const failedSessionId = historyErrorSessionId.value
          const failedHistoryError = historyError.value
          resetConversation(true)
          historyErrorSessionId.value = failedSessionId
          historyError.value = failedHistoryError
        }
      } else {
        newSession()
      }
    }
    notify('success', '会话已删除', '聊天记录与 Agent 状态已同步清除。')
  } catch (error) {
    notify('error', '删除会话失败', sessionOperationFailureDetail())
    throw error
  }
}

async function sendMessage(rawMessage: string): Promise<void> {
  const content = rawMessage.trim()
  if (!content || sending.value) return

  const turnId = generateUuid()
  const isNewSession = !sessions.value.some((item) => item.session_id === sessionId.value)
  const userMessage: UiMessage = {
    id: `${turnId}:user`,
    role: 'user',
    content,
    turn_id: turnId,
    message_id: `${turnId}:user`,
    citations: [],
    state: 'complete',
    answerState: 'complete',
  }
  const assistant: UiMessage = {
    id: `${turnId}:assistant`,
    role: 'assistant',
    content: '',
    turn_id: turnId,
    message_id: `${turnId}:assistant`,
    citations: [],
    state: 'streaming',
    answerState: 'streaming',
    failureType: null,
    failureStage: null,
    failureReason: null,
    traceId: null,
  }
  messages.value.push(userMessage, assistant)
  agentSteps.value = []
  activeCitations.value = []
  sending.value = true
  sessionTitlePending.value = isNewSession
  rememberSessionMode(sessionId.value, chatMode.value)
  activeController = new AbortController()
  let streamState = createStreamState()

  try {
    await streamChat(
      {
        message: content,
        user_id: userId,
        session_id: sessionId.value,
        mode: chatMode.value,
        web_search: webSearchEnabled.value,
        turn_id: turnId,
      },
      (event: StreamEvent) => {
        streamState = reduceStreamEvent(streamState, event)
        syncAssistantMessage(assistant, streamState)
        agentSteps.value = streamState.steps
      },
      activeController.signal,
    )
    if (!streamState.resultReceived) {
      throw new Error('后端没有返回经过校验的最终结果。')
    }
    backendOnline.value = true
    await loadSessions()
  } catch (error) {
    if (streamState.resultReceived) {
      // The result event is authoritative. A disconnect after it must not
      // turn a delivered answer back into an error or cancelled state.
      syncAssistantMessage(assistant, streamState)
      backendOnline.value = true
    } else if (isAbortError(error)) {
      assistant.content = ''
      assistant.answerState = 'cancelled'
      assistant.state = 'cancelled'
      assistant.failureType = null
      assistant.failureStage = null
      assistant.failureReason = null
      assistant.traceId = streamState.traceId
      agentSteps.value = []
      notify('info', '已取消本次回答')
    } else {
      assistant.content = ''
      assistant.answerState = 'error'
      assistant.state = 'error'
      assistant.failureType = null
      assistant.failureStage = null
      assistant.failureReason = '本次请求未收到经过校验的最终回答。'
      assistant.traceId = streamState.traceId
      backendOnline.value = false
      notify('error', '问答请求失败', chatFailureDetail())
    }
  } finally {
    activeController = null
    sending.value = false
    sessionTitlePending.value = false
  }
}

function cancelMessage(): void {
  activeController?.abort()
}

async function retryMessage(message: UiMessage): Promise<void> {
  if (sending.value || message.role !== 'assistant') return
  const messageIndex = messages.value.findIndex((item) => item.id === message.id)
  const previousMessage = messageIndex > 0 ? messages.value[messageIndex - 1] : undefined
  if (previousMessage?.role !== 'user' || !previousMessage.content.trim()) {
    notify('warning', '无法重新发送', '没有找到本次回答对应的问题。')
    return
  }
  // Persisted search failures identify a web request even after the session's
  // search toggle (or its locally remembered mode) has been reset.
  if (['search_error', 'search_answer_error', 'search_no_results'].includes(message.failureType || '')) {
    chatMode.value = 'general'
    webSearchEnabled.value = true
  }
  await sendMessage(previousMessage.content)
}

function showCitations(citations: Citation[]): void {
  activeCitations.value = citations
}

async function uploadKnowledge(
  file: File,
  title: string,
  source: string,
  metadata: KnowledgeUploadMetadata = {},
): Promise<UploadResponse> {
  uploading.value = true
  try {
    const response = await uploadKnowledgeRequest(file, title, source, metadata)
    await loadKnowledge()
    if (response.record.deduplicated) {
      notify('warning', '检测到重复文件', response.message)
    } else {
      notify('success', '文件已完成入库', response.message)
    }
    return response
  } catch (error) {
    notify('error', '文件上传失败', readableKnowledgeUploadError(error))
    throw error
  } finally {
    uploading.value = false
  }
}

export function useWorkspace() {
  return {
    userId,
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
    chatMode,
    webSearchEnabled,
    uploading,
    sessionTitlePending,
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
