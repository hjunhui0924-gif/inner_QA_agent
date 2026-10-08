import { computed, ref, reactive, nextTick } from 'vue'

import { fetchHealth, streamChat } from '../services/api'
import type {
  AgentStep,
  Citation,
  SessionSummary,
  StreamEvent,
  UiMessage,
  ChatMode,
  TurnProcessSummary,
} from '../types/api'
import {
  beginTurnTiming,
  afterPaint,
  recordRefresh,
  recordFirstToken,
  recordStreamUpdate,
} from '../utils/performance'
import { createNotificationState } from '../state/notificationState'
import { createSessionState } from '../state/sessionState'
import { createChatState } from '../state/chatState'
import { createKnowledgeState } from '../state/knowledgeState'
import { createContextUsageState } from '../state/contextUsageState'
import { createContextActions } from '../state/contextActions'
import { createKnowledgeActions } from '../state/knowledgeActions'
import { createSessionActions } from '../state/sessionActions'
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
  sendingStartedAt, chatMode, webSearchEnabled,
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
let activeController: AbortController | null = null

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

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
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
  assistant.processSummary = processSummaryFromStream(streamState)
  if (streamState.resultReceived) {
    assistant.content = streamState.content
    assistant.citations = streamState.citations
    activeCitations.value = streamState.citations
  }
}

function processSummaryFromStream(
  streamState: StreamUiState,
  forcedStatus?: 'error' | 'cancelled',
): TurnProcessSummary {
  const steps = streamState.steps.map((step) => ({
    node: step.node,
    status: step.status || 'pending',
    duration_ms: step.startedAt && step.endedAt
      ? Math.max(0, step.endedAt - step.startedAt)
      : null,
  }))
  const status: TurnProcessSummary['status'] = forcedStatus || (streamState.answerState === 'fallback' ? 'fallback'
    : streamState.answerState === 'error' ? 'error'
      : streamState.answerState === 'cancelled' ? 'cancelled' : 'complete')
  return {
    status,
    elapsed_ms: null,
    steps,
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
  invalidateSessions()
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
    processSummary: null,
  })
  messages.value.push(userMessage, assistant)
  agentSteps.value = []
  activeCitations.value = []
  sendingStartedAt.value = Date.now()
  sending.value = true
  sessionTitlePending.value = isNewSession
  rememberSessionMode(sessionId.value, chatMode.value)
  const controller = new AbortController()
  activeController = controller
  let requestFinished = false
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
        if (requestFinished || controller.signal.aborted || streamState.resultReceived) return
        recordStreamUpdate(timing)
        if (event.type === 'status' && timing.firstStatusMs === undefined)
          timing.firstStatusMs = performance.now() - timing.started
        if (event.type === 'preview_delta' || event.type === 'token') recordFirstToken(timing)
        if (event.type === 'result') {
          timing.resultMs = performance.now() - timing.started
          void nextTick(() =>
            afterPaint(() => {
              if (chatVisible.value)
                timing.visibleMs = timing.firstVisibleAnswerMs = performance.now() - timing.started
            }),
          )
        }
        if (event.type === 'preview_delta' && (attempt.mode !== 'general' || attempt.web_search)) return
        streamState = reduceStreamEvent(streamState, event)
        if (event.type === 'result' && event.context_usage) {
          contextUsage.value = event.context_usage
          contextUsageStale.value = event.context_usage.status === 'unavailable'
        }
        syncAssistantMessage(assistant, streamState)
        agentSteps.value = streamState.steps
      },
      controller.signal,
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
      assistant.processSummary = processSummaryFromStream(streamState, 'cancelled')
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
      assistant.processSummary = processSummaryFromStream(streamState, 'error')
      backendOnline.value = false
      notify('error', '问答请求失败', readableError(error))
    }
  } finally {
    requestFinished = true
    if (activeController === controller) activeController = null
    sending.value = false
    timing.readyMs = performance.now() - timing.started
    sessionTitlePending.value = false
    if (streamState.resultReceived) {
      void loadSessions()
      void refreshContextUsage()
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
