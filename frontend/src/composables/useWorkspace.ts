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
} from '../types/api'

const userId = 'user_001'
const sessionId = ref<string>(crypto.randomUUID())
const sessions = ref<SessionSummary[]>([])
const messages = ref<UiMessage[]>([])
const knowledgeRecords = ref<KnowledgeRecord[]>([])
const agentSteps = ref<AgentStep[]>([])
const activeCitations = ref<Citation[]>([])
const backendOnline = ref(false)
const initialized = ref(false)
const loadingSessions = ref(false)
const loadingHistory = ref(false)
const loadingKnowledge = ref(false)
const sending = ref(false)
const chatMode = ref<ChatMode>('knowledge')
const webSearchEnabled = ref(false)
const uploading = ref(false)
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
  return `${prefix}-${crypto.randomUUID()}`
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

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

async function loadSessions(): Promise<void> {
  loadingSessions.value = true
  try {
    sessions.value = await fetchSessions(userId)
  } catch (error) {
    notify('error', '会话列表加载失败', readableError(error))
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

function newSession(): void {
  if (sending.value) return
  historyRequestGeneration += 1
  loadingHistory.value = false
  sessionId.value = crypto.randomUUID()
  messages.value = []
  agentSteps.value = []
  activeCitations.value = []
  chatMode.value = 'knowledge'
  webSearchEnabled.value = false
}

async function openSession(targetSessionId: string): Promise<void> {
  if (sending.value || loadingHistory.value || targetSessionId === sessionId.value) return
  const requestGeneration = ++historyRequestGeneration
  loadingHistory.value = true
  try {
    const history = await fetchHistory(userId, targetSessionId)
    if (requestGeneration !== historyRequestGeneration) return
    sessionId.value = targetSessionId
    chatMode.value = readSessionModes()[targetSessionId] ?? 'knowledge'
    webSearchEnabled.value = false
    messages.value = history.map((message) => ({
      ...message,
      id: createId(message.role),
      citations: message.citations ?? [],
      state: 'complete',
    }))
    activeCitations.value =
      [...messages.value].reverse().find((message) => message.citations?.length)?.citations ?? []
    agentSteps.value = []
  } catch (error) {
    if (requestGeneration !== historyRequestGeneration) return
    notify('error', '会话加载失败', readableError(error))
  } finally {
    if (requestGeneration === historyRequestGeneration) loadingHistory.value = false
  }
}

async function removeSession(targetSessionId: string): Promise<void> {
  historyRequestGeneration += 1
  loadingHistory.value = false
  try {
    await deleteSessionRequest(userId, targetSessionId)
    sessions.value = sessions.value.filter((item) => item.session_id !== targetSessionId)
    if (sessionId.value === targetSessionId) newSession()
    notify('success', '会话已删除', '聊天记录与 Agent 状态已同步清除。')
  } catch (error) {
    notify('error', '删除会话失败', readableError(error))
    throw error
  }
}

function applyStreamEvent(event: StreamEvent, assistant: UiMessage): void {
  if (event.type === 'status') {
    if (agentSteps.value.at(-1)?.node !== event.node) {
      agentSteps.value.push({ node: event.node, content: event.content })
    }
    if (event.node === 'error') assistant.state = 'error'
  } else if (event.type === 'token') {
    assistant.content += event.content
  } else if (event.type === 'result') {
    assistant.content = event.content
    assistant.citations = event.citations ?? []
    assistant.failureType = event.failure_type
    assistant.state = event.failure_type === 'none' ? 'complete' : 'error'
    activeCitations.value = assistant.citations
  }
}

async function sendMessage(rawMessage: string): Promise<void> {
  const content = rawMessage.trim()
  if (!content || sending.value) return

  const userMessage: UiMessage = {
    id: createId('user'),
    role: 'user',
    content,
    citations: [],
    state: 'complete',
  }
  const assistant: UiMessage = {
    id: createId('assistant'),
    role: 'assistant',
    content: '',
    citations: [],
    state: 'streaming',
  }
  messages.value.push(userMessage, assistant)
  agentSteps.value = []
  activeCitations.value = []
  sending.value = true
  rememberSessionMode(sessionId.value, chatMode.value)
  activeController = new AbortController()

  try {
    await streamChat(
      {
        message: content,
        user_id: userId,
        session_id: sessionId.value,
        mode: chatMode.value,
        web_search: webSearchEnabled.value,
      },
      (event) => applyStreamEvent(event, assistant),
      activeController.signal,
    )
    if (!assistant.content.trim()) {
      throw new Error('后端没有返回有效回答。')
    }
    backendOnline.value = true
    await loadSessions()
  } catch (error) {
    assistant.state = 'error'
    if (!assistant.content) assistant.content = readableError(error)
    if (isAbortError(error)) {
      notify('info', '已取消本次回答')
    } else {
      backendOnline.value = false
      notify('error', '问答请求失败', readableError(error))
    }
  } finally {
    activeController = null
    sending.value = false
  }
}

function cancelMessage(): void {
  activeController?.abort()
}

function showCitations(citations: Citation[]): void {
  activeCitations.value = citations
}

async function uploadKnowledge(
  file: File,
  title: string,
  source: string,
): Promise<UploadResponse> {
  uploading.value = true
  try {
    const response = await uploadKnowledgeRequest(file, title, source)
    await loadKnowledge()
    if (response.record.deduplicated) {
      notify('warning', '检测到重复文件', response.message)
    } else {
      notify('success', '文件已完成入库', response.message)
    }
    return response
  } catch (error) {
    notify('error', '文件上传失败', readableError(error))
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
    loadingHistory,
    loadingKnowledge,
    sending,
    chatMode,
    webSearchEnabled,
    uploading,
    toasts,
    initialize,
    loadSessions,
    loadKnowledge,
    newSession,
    openSession,
    removeSession,
    sendMessage,
    cancelMessage,
    showCitations,
    uploadKnowledge,
    dismissToast,
  }
}
