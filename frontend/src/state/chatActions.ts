import { nextTick, reactive, type Ref } from 'vue'

import { streamChat } from '../services/api'
import type { AgentStep, Citation, ChatMode, SessionSummary, UiMessage } from '../types/api'
import { beginTurnTiming, afterPaint, recordFirstToken, recordStreamUpdate } from '../utils/performance'
import { createStreamState, reduceStreamEvent, type StreamUiState } from '../utils/streamState'
import { processSummaryFromStream, uiStateForAnswer } from './chatPresentation'

interface ChatActionOptions {
  userId: string
  generateId: () => string
  sessionId: Ref<string>
  sessions: Ref<SessionSummary[]>
  messages: Ref<UiMessage[]>
  agentSteps: Ref<AgentStep[]>
  activeCitations: Ref<Citation[]>
  backendOnline: Ref<boolean>
  sending: Ref<boolean>
  sendingStartedAt: Ref<number>
  chatMode: Ref<ChatMode>
  webSearchEnabled: Ref<boolean>
  sessionTitlePending: Ref<boolean>
  chatVisible: Ref<boolean>
  notify: (tone: 'success' | 'warning' | 'error' | 'info', title: string, detail?: string) => void
  readableError: (error: unknown) => string
  isAbortError: (error: unknown) => boolean
  cancelTitleRefresh: () => void
  rememberSessionMode: (sessionId: string, mode: ChatMode) => void
  loadSessions: () => Promise<void>
  refreshContextUsage: () => Promise<void>
  refreshTitleLater: () => void
  invalidateSessions: () => void
}

export function createChatActions(options: ChatActionOptions) {
  let activeController: AbortController | null = null

  function syncAssistantMessage(assistant: UiMessage, streamState: StreamUiState): void {
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
      options.activeCitations.value = streamState.citations
    }
  }

  async function sendMessage(rawMessage: string, retryOf?: UiMessage): Promise<void> {
    const content = rawMessage.trim()
    if (!content || content.length > 12000 || options.sending.value) return
    options.cancelTitleRefresh()
    options.invalidateSessions()
    const turnId = options.generateId()
    const groupId = retryOf?.attempt_group_id || retryOf?.turn_id || turnId
    const attempt = {
      attempt_group_id: groupId,
      retry_of_turn_id: retryOf?.turn_id,
      mode: options.chatMode.value,
      web_search: options.webSearchEnabled.value,
    }
    const timing = beginTurnTiming(turnId)
    void nextTick(() => afterPaint(() => {
      if (options.chatVisible.value) timing.feedbackMs = performance.now() - timing.started
    }))
    const isNewSession = !options.sessions.value.some((item) => item.session_id === options.sessionId.value)
    const userMessage: UiMessage = {
      ...attempt, id: `${turnId}:user`, role: 'user', content,
      turn_id: turnId, message_id: `${turnId}:user`, citations: [], state: 'complete', answerState: 'complete',
    }
    const assistant = reactive<UiMessage>({
      ...attempt, id: `${turnId}:assistant`, role: 'assistant', content: '',
      turn_id: turnId, message_id: `${turnId}:assistant`, citations: [], state: 'streaming', answerState: 'streaming',
      failureType: null, failureStage: null, failureReason: null, traceId: null, processSummary: null,
    })
    options.messages.value.push(userMessage, assistant)
    options.agentSteps.value = []
    options.activeCitations.value = []
    options.sendingStartedAt.value = Date.now()
    options.sending.value = true
    options.sessionTitlePending.value = isNewSession
    options.rememberSessionMode(options.sessionId.value, options.chatMode.value)
    const controller = new AbortController()
    activeController = controller
    let requestFinished = false
    let streamState = createStreamState()
    try {
      await streamChat({
        message: content, user_id: options.userId, session_id: options.sessionId.value,
        mode: options.chatMode.value, web_search: options.webSearchEnabled.value,
        turn_id: turnId, attempt_group_id: groupId, retry_of_turn_id: retryOf?.turn_id || undefined,
      }, (event) => {
        if (requestFinished || controller.signal.aborted || streamState.resultReceived) return
        recordStreamUpdate(timing)
        if (event.type === 'status' && timing.firstStatusMs === undefined)
          timing.firstStatusMs = performance.now() - timing.started
        if (event.type === 'preview_delta' || event.type === 'token') recordFirstToken(timing)
        if (event.type === 'result') {
          timing.resultMs = performance.now() - timing.started
          void nextTick(() => afterPaint(() => {
            if (options.chatVisible.value) timing.visibleMs = timing.firstVisibleAnswerMs = performance.now() - timing.started
          }))
        }
        if (event.type === 'preview_delta' && (attempt.mode !== 'general' || attempt.web_search)) return
        streamState = reduceStreamEvent(streamState, event)
        syncAssistantMessage(assistant, streamState)
        options.agentSteps.value = streamState.steps
      }, controller.signal)
      timing.streamEndedMs = performance.now() - timing.started
      if (!streamState.resultReceived) throw new Error('后端没有返回经过校验的最终结果。')
      options.backendOnline.value = true
    } catch (error) {
      timing.streamEndedMs = performance.now() - timing.started
      if (streamState.resultReceived) {
        syncAssistantMessage(assistant, streamState)
        options.backendOnline.value = true
      } else if (options.isAbortError(error)) {
        assistant.content = ''; assistant.answerState = 'cancelled'; assistant.state = 'cancelled'
        assistant.failureType = null; assistant.failureStage = null; assistant.failureReason = null
        assistant.traceId = streamState.traceId
        assistant.processSummary = processSummaryFromStream(streamState, 'cancelled')
        options.agentSteps.value = []
        options.notify('info', '已取消本次回答')
      } else {
        assistant.content = ''; assistant.answerState = 'error'; assistant.state = 'error'
        assistant.failureType = null; assistant.failureStage = null
        assistant.failureReason = '本次请求未收到经过校验的最终回答。'
        assistant.traceId = streamState.traceId
        assistant.processSummary = processSummaryFromStream(streamState, 'error')
        options.backendOnline.value = false
        options.notify('error', '问答请求失败', options.readableError(error))
      }
    } finally {
      requestFinished = true
      if (activeController === controller) activeController = null
      options.sending.value = false
      timing.readyMs = performance.now() - timing.started
      options.sessionTitlePending.value = false
      if (streamState.resultReceived) {
        void options.loadSessions()
        void options.refreshContextUsage()
        if (isNewSession) options.refreshTitleLater()
      }
    }
  }

  function cancelMessage(): void { activeController?.abort() }

  async function retryMessage(message: UiMessage, retryOptions: { withoutWeb?: boolean } = {}): Promise<void> {
    if (options.sending.value || message.role !== 'assistant') return
    const index = options.messages.value.findIndex((item) => item.id === message.id)
    const previous = index > 0 ? options.messages.value[index - 1] : undefined
    if (previous?.role !== 'user' || !previous.content.trim()) {
      options.notify('warning', '无法重新发送', '没有找到本次回答对应的问题。')
      return
    }
    if (message.mode) options.chatMode.value = message.mode
    if (message.web_search !== undefined) options.webSearchEnabled.value = message.web_search
    if (['search_error', 'search_answer_error', 'search_no_results'].includes(message.failureType || '')) {
      options.chatMode.value = 'general'
      options.webSearchEnabled.value = !retryOptions.withoutWeb
    }
    if (retryOptions.withoutWeb) {
      options.chatMode.value = 'general'
      options.webSearchEnabled.value = false
    }
    await sendMessage(previous.content, message)
  }

  return { sendMessage, cancelMessage, retryMessage }
}
