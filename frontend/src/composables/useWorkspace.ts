import { computed, ref, reactive, nextTick } from 'vue'

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
import {
  beginTurnTiming,
  afterPaint,
  recordRefresh,
} from '../utils/performance'
import { readableKnowledgeUploadError } from '../utils/knowledge'
import { nextSessionAfterDelete } from '../utils/sessions'
import {
  createStreamState,
  reduceStreamEvent,
  type StreamUiState,
} from '../utils/streamState'

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

const sessionId = ref<string>(generateUuid())
const sessions = ref<SessionSummary[]>([])
const messages = ref<UiMessage[]>([])
const knowledgeRecords = ref<KnowledgeRecord[]>([])
const agentSteps = ref<AgentStep[]>([])
const activeCitations = ref<Citation[]>([])
const backendOnline = ref(false)
const initialized = ref(false)
const chatVisible = ref(false)
const viewCache = reactive<
  Record<string, { draft: string; scrollTop: number }>
>({})
const currentView = computed(() => {
  if (!viewCache[sessionId.value])
    viewCache[sessionId.value] = { draft: '', scrollTop: 0 }
  return viewCache[sessionId.value]!
})
const draft = computed({
  get: () => currentView.value.draft,
  set: (value: string) => {
    currentView.value.draft = value
  },
})
let sessionsGeneration = 0
let initialization: Promise<void> | null = null
let chatInitialized = false
let knowledgeInitialized = false
let chatInitialization: Promise<void> | null = null
let knowledgeInitialization: Promise<void> | null = null
let titleTimers: ReturnType<typeof setTimeout>[] = []
function cancelTitleRefresh() {
  titleTimers.forEach(clearTimeout)
  titleTimers = []
}
function setChatVisible(visible: boolean) {
  chatVisible.value = visible
  if (!visible) cancelTitleRefresh()
  else if (chatInitialized) void loadSessions()
}
function refreshTitleLater() {
  cancelTitleRefresh()
  if (chatVisible.value)
    titleTimers = [2000, 5000, 10000].map((delay) =>
      setTimeout(() => {
        if (chatVisible.value) void loadSessions()
      }, delay),
    )
}
const loadingSessions = ref(false)
const sessionsError = ref<string | null>(null)
const loadingHistory = ref(false)
const historyError = ref<string | null>(null)
const historyErrorSessionId = ref<string | null>(null)
const loadingKnowledge = ref(false)
const sending = ref(false)
const sendingStartedAt = ref(0)
const chatMode = ref<ChatMode>('knowledge')
const webSearchEnabled = ref(false)
const uploading = ref(false)
const knowledgeError = ref('')
let knowledgeGeneration = 0
const uploadForm = reactive({
  title: '',
  source: 'internal_upload',
  department: 'unknown',
  version: 'v1',
  status: 'active',
  effectiveFrom: '',
  effectiveTo: '',
  owner: '',
  selectedFile: null as File | null,
  uploadError: '',
  lastUpload: null as UploadResponse | null,
})
const uploadTask = reactive({
  file: null as File | null,
  metadata: {} as KnowledgeUploadMetadata,
  result: null as UploadResponse | null,
  error: '',
  title: '',
  source: '',
})
const sessionTitlePending = ref(false)
const toasts = ref<ToastMessage[]>([])
let activeController: AbortController | null = null
let historyRequestGeneration = 0
const sessionModeStorageKey = 'enterprise-assistant-session-modes'

function readSessionModes(): Record<string, ChatMode> {
  try {
    return JSON.parse(
      localStorage.getItem(sessionModeStorageKey) || '{}',
    ) as Record<string, ChatMode>
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
  () =>
    sessions.value.find((item) => item.session_id === sessionId.value) ?? null,
)
const activeSessionTitle = computed(
  () => activeSession.value?.title || '新会话',
)

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

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

function uiStateForAnswer(
  answerState: UiMessage['answerState'],
): UiMessage['state'] {
  if (answerState === 'error') return 'error'
  if (answerState === 'cancelled') return 'cancelled'
  if (answerState === 'complete' || answerState === 'fallback')
    return 'complete'
  return 'streaming'
}

function syncAssistantMessage(
  assistant: UiMessage,
  streamState: StreamUiState,
): void {
  assistant.previewContent = streamState.previewContent
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
  const started = performance.now()
  const generation = ++sessionsGeneration
  loadingSessions.value = true
  sessionsError.value = null
  try {
    const result = await fetchSessions(userId)
    if (generation === sessionsGeneration) {
      sessions.value = result
      chatInitialized = true
    }
  } catch {
    if (generation === sessionsGeneration)
      sessionsError.value = '会话列表暂时无法加载，请重试。'
  } finally {
    recordRefresh(performance.now() - started)
    if (generation === sessionsGeneration) loadingSessions.value = false
  }
}

async function loadKnowledge(): Promise<void> {
  const generation = ++knowledgeGeneration
  loadingKnowledge.value = true
  knowledgeError.value = ''
  try {
    const records = await fetchKnowledgeRecords()
    if (generation === knowledgeGeneration) {
      knowledgeRecords.value = records
      knowledgeInitialized = true
    }
  } catch (error) {
    if (generation === knowledgeGeneration)
      knowledgeError.value = `知识列表加载失败。${readableError(error)}`
  } finally {
    if (generation === knowledgeGeneration) loadingKnowledge.value = false
  }
}

async function checkHealth(): Promise<void> {
  if (initialized.value && backendOnline.value) return
  if (!initialization)
    initialization = (async () => {
      try {
        await fetchHealth()
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
  if (target === 'chat' && !chatInitialized) {
    if (!chatInitialization)
      chatInitialization = loadSessions().finally(() => {
        chatInitialization = null
      })
    await chatInitialization
  }
  if (target === 'knowledge' && !knowledgeInitialized) {
    if (!knowledgeInitialization)
      knowledgeInitialization = loadKnowledge().finally(() => {
        knowledgeInitialization = null
      })
    await knowledgeInitialization
  }
  await health
}

function resetConversation(preserveHistoryError = false): void {
  cancelTitleRefresh()
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
  cancelTitleRefresh()
  const requestGeneration = ++historyRequestGeneration
  loadingHistory.value = true
  historyError.value = null
  historyErrorSessionId.value = null
  try {
    const history = await fetchHistory(userId, targetSessionId)
    if (requestGeneration !== historyRequestGeneration) return false
    sessionId.value = targetSessionId
    chatMode.value = history.find((message) => message.mode)?.mode ?? readSessionModes()[targetSessionId] ?? 'knowledge'
    webSearchEnabled.value = false
    messages.value = history.map((message) => ({
      ...message,
      id: message.message_id || createId(message.role),
      citations: message.citations ?? [],
      state: 'complete',
      answerState:
        message.failure_type && message.failure_type !== 'none'
          ? 'fallback'
          : 'complete',
      failureType: message.failure_type ?? null,
      failureStage: message.failure_stage ?? null,
      failureReason: message.failure_reason ?? null,
      traceId: message.trace_id ?? null,
    }))
    activeCitations.value =
      [...messages.value].reverse().find((message) => message.citations?.length)
        ?.citations ?? []
    agentSteps.value = []
    return true
  } catch (error) {
    if (requestGeneration !== historyRequestGeneration) return false
    historyError.value = '会话内容暂时无法加载，请重试。'
    historyErrorSessionId.value = targetSessionId
    notify('error', '会话加载失败', historyError.value)
    return false
  } finally {
    if (requestGeneration === historyRequestGeneration)
      loadingHistory.value = false
  }
}

async function retryOpenSession(): Promise<boolean> {
  if (!historyErrorSessionId.value) return false
  return openSession(historyErrorSessionId.value)
}

async function removeSession(targetSessionId: string): Promise<void> {
  if (sending.value) return
  cancelTitleRefresh()
  ++sessionsGeneration
  const sessionsBeforeDelete = [...sessions.value]
  const wasActive = sessionId.value === targetSessionId
  historyRequestGeneration += 1
  loadingHistory.value = false
  historyError.value = null
  historyErrorSessionId.value = null
  try {
    await deleteSessionRequest(userId, targetSessionId)
    ++sessionsGeneration
    loadingSessions.value = false
    delete viewCache[targetSessionId]
    sessions.value = sessions.value.filter(
      (item) => item.session_id !== targetSessionId,
    )
    if (wasActive) {
      const nextSessionId = nextSessionAfterDelete(
        sessionsBeforeDelete,
        targetSessionId,
      )
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

async function sendMessage(rawMessage: string, retryOf?: UiMessage): Promise<void> {
  const content = rawMessage.trim()
  if (
    !content ||
    content.length > 12000 ||
    sending.value ||
    loadingHistory.value
  )
    return
  cancelTitleRefresh()
  ++sessionsGeneration
  loadingSessions.value = false

  const turnId = generateUuid()
  const groupId = retryOf?.attempt_group_id || retryOf?.turn_id || turnId
  const attempt = { attempt_group_id: groupId, retry_of_turn_id: retryOf?.turn_id, mode: chatMode.value, web_search: webSearchEnabled.value }
  const timing = beginTurnTiming(turnId)
  void nextTick(() =>
    afterPaint(() => {
      if (chatVisible.value)
        timing.feedbackMs = performance.now() - timing.started
    }),
  )
  const isNewSession = !sessions.value.some(
    (item) => item.session_id === sessionId.value,
  )
  const userMessage: UiMessage = {
    ...attempt,
    id: `${turnId}:user`,
    role: 'user',
    content,
    turn_id: turnId,
    message_id: `${turnId}:user`,
    citations: [],
    state: 'complete',
    answerState: 'complete',
  }
  const assistant = reactive<UiMessage>({
    ...attempt,
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
  })
  messages.value.push(userMessage, assistant)
  agentSteps.value = []
  activeCitations.value = []
  sendingStartedAt.value = Date.now()
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
        attempt_group_id: groupId,
        retry_of_turn_id: retryOf?.turn_id || undefined,
      },
      (event: StreamEvent) => {
        if (event.type === 'status' && timing.firstStatusMs === undefined)
          timing.firstStatusMs = performance.now() - timing.started
        if (event.type === 'result') {
          timing.resultMs = performance.now() - timing.started
          void nextTick(() =>
            afterPaint(() => {
              if (chatVisible.value)
                timing.visibleMs = performance.now() - timing.started
            }),
          )
        }
        if (event.type === 'preview_delta' && (attempt.mode !== 'general' || attempt.web_search)) return
        streamState = reduceStreamEvent(streamState, event)
        syncAssistantMessage(assistant, streamState)
        agentSteps.value = streamState.steps
      },
      activeController.signal,
    )
    timing.streamEndedMs = performance.now() - timing.started
    if (!streamState.resultReceived) {
      throw new Error('后端没有返回经过校验的最终结果。')
    }
    backendOnline.value = true
  } catch (error) {
    timing.streamEndedMs = performance.now() - timing.started
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
      notify('error', '问答请求失败', readableError(error))
    }
  } finally {
    activeController = null
    sending.value = false
    timing.readyMs = performance.now() - timing.started
    sessionTitlePending.value = false
    if (streamState.resultReceived) {
      void loadSessions()
      if (isNewSession) refreshTitleLater()
    }
  }
}

function cancelMessage(): void {
  activeController?.abort()
}

async function retryMessage(message: UiMessage, options: { withoutWeb?: boolean } = {}): Promise<void> {
  if (sending.value || message.role !== 'assistant') return
  const messageIndex = messages.value.findIndex(
    (item) => item.id === message.id,
  )
  const previousMessage =
    messageIndex > 0 ? messages.value[messageIndex - 1] : undefined
  if (previousMessage?.role !== 'user' || !previousMessage.content.trim()) {
    notify('warning', '无法重新发送', '没有找到本次回答对应的问题。')
    return
  }
  if (message.mode) chatMode.value = message.mode
  if (message.web_search !== undefined) webSearchEnabled.value = message.web_search
  // Persisted search failures identify a web request even after the session's
  // search toggle (or its locally remembered mode) has been reset.
  if (
    ['search_error', 'search_answer_error', 'search_no_results'].includes(
      message.failureType || '',
    )
  ) {
    chatMode.value = 'general'
    webSearchEnabled.value = !options.withoutWeb
  }
  if (options.withoutWeb) {
    chatMode.value = 'general'
    webSearchEnabled.value = false
  }
  await sendMessage(previousMessage.content, message)
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
  if (uploading.value) throw new Error('已有文档正在上传并处理。')
  uploading.value = true
  Object.assign(uploadTask, {
    file,
    title,
    source,
    metadata: { ...metadata },
    result: null,
    error: '',
  })
  uploadForm.uploadError = ''
  try {
    const response = await uploadKnowledgeRequest(file, title, source, metadata)
    uploadTask.result = response
    uploadForm.lastUpload = response
    uploadForm.selectedFile = null
    uploadForm.title = ''
    await loadKnowledge()
    if (response.record.deduplicated) {
      notify('warning', '检测到重复文件', response.message)
    } else {
      notify('success', '文件已完成入库', response.message)
    }
    return response
  } catch (error) {
    uploadTask.error = readableKnowledgeUploadError(error)
    uploadForm.uploadError = uploadTask.error
    notify('error', '文件上传失败', uploadTask.error)
    throw error
  } finally {
    uploadTask.file = null
    uploading.value = false
  }
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
    uploading,
    uploadTask,
    uploadForm,
    knowledgeError,
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
