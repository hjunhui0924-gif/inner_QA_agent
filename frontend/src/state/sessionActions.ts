import { ref, type Ref } from 'vue'

import { deleteSession as deleteSessionRequest, fetchHistory, fetchSessions } from '../services/api'
import type { ChatMode } from '../types/api'
import { recordRefresh } from '../utils/performance'
import { nextSessionAfterDelete } from '../utils/sessions'
import type { createChatState } from './chatState'
import type { createContextUsageState } from './contextUsageState'
import type { createSessionState } from './sessionState'

interface SessionActionOptions {
  userId: string
  state: ReturnType<typeof createSessionState>
  chat: ReturnType<typeof createChatState>
  context: ReturnType<typeof createContextUsageState>
  generateId: () => string
  notify: (tone: 'success' | 'warning' | 'error' | 'info', title: string, detail?: string) => void
  refreshContextUsage: (sessionId?: string) => Promise<void>
  sessionOperationFailureDetail: () => string
  sending: Ref<boolean>
}

export function createSessionActions(options: SessionActionOptions) {
  let sessionsGeneration = 0
  let historyRequestGeneration = 0
  let chatInitialized = false
  const initialized = ref(false)
  let titleTimers: ReturnType<typeof setTimeout>[] = []
  const sessionModeStorageKey = 'enterprise-assistant-session-modes'

  function cancelTitleRefresh(): void {
    titleTimers.forEach(clearTimeout)
    titleTimers = []
  }
  function readSessionModes(): Record<string, ChatMode> {
    try {
      return JSON.parse(localStorage.getItem(sessionModeStorageKey) || '{}') as Record<string, ChatMode>
    } catch { return {} }
  }
  function rememberSessionMode(sessionId: string, mode: ChatMode): void {
    localStorage.setItem(sessionModeStorageKey, JSON.stringify({ ...readSessionModes(), [sessionId]: mode }))
  }
  function setChatVisible(visible: boolean): void {
    options.state.chatVisible.value = visible
    if (!visible) cancelTitleRefresh()
    else if (chatInitialized) void loadSessions()
  }
  function refreshTitleLater(): void {
    cancelTitleRefresh()
    if (options.state.chatVisible.value)
      titleTimers = [2000, 5000, 10000].map((delay) => setTimeout(() => {
        if (options.state.chatVisible.value) void loadSessions()
      }, delay))
  }
  async function loadSessions(): Promise<void> {
    const started = performance.now()
    const generation = ++sessionsGeneration
    options.state.loadingSessions.value = true
    options.state.sessionsError.value = null
    try {
      const result = await fetchSessions(options.userId)
      if (generation === sessionsGeneration) {
        options.state.sessions.value = result
        chatInitialized = true
        initialized.value = true
      }
    } catch {
      if (generation === sessionsGeneration) options.state.sessionsError.value = '会话列表暂时无法加载，请重试。'
    } finally {
      recordRefresh(performance.now() - started)
      if (generation === sessionsGeneration) options.state.loadingSessions.value = false
    }
  }
  function resetConversation(preserveHistoryError = false): void {
    cancelTitleRefresh()
    historyRequestGeneration += 1
    options.state.loadingHistory.value = false
    options.state.sessionId.value = options.generateId()
    options.chat.messages.value = []
    options.chat.agentSteps.value = []
    options.chat.activeCitations.value = []
    options.state.sessionTitlePending.value = false
    if (!preserveHistoryError) {
      options.state.historyError.value = null
      options.state.historyErrorSessionId.value = null
    }
    options.chat.chatMode.value = 'knowledge'
    options.chat.webSearchEnabled.value = false
    options.context.contextUsage.value = null
    options.context.contextUsageStale.value = false
    void options.refreshContextUsage()
  }
  function newSession(): void { if (!options.sending.value) resetConversation() }
  async function openSession(targetSessionId: string): Promise<boolean> {
    if (options.sending.value || options.state.loadingHistory.value) return false
    if (targetSessionId === options.state.sessionId.value) {
      options.state.historyError.value = null
      options.state.historyErrorSessionId.value = null
      return true
    }
    cancelTitleRefresh()
    const requestGeneration = ++historyRequestGeneration
    options.state.loadingHistory.value = true
    options.state.historyError.value = null
    options.state.historyErrorSessionId.value = null
    try {
      const history = await fetchHistory(options.userId, targetSessionId)
      if (requestGeneration !== historyRequestGeneration) return false
      options.state.sessionId.value = targetSessionId
      options.chat.chatMode.value = history.find((message) => message.mode)?.mode ?? readSessionModes()[targetSessionId] ?? 'knowledge'
      options.chat.webSearchEnabled.value = false
      options.chat.modelProvider.value = history.find((message) => message.model_provider)?.model_provider ?? options.chat.modelProvider.value
      options.chat.messages.value = history.map((message) => ({
        ...message,
        id: message.message_id || options.generateId(),
        citations: message.citations ?? [],
        state: 'complete',
        answerState: message.failure_type && message.failure_type !== 'none' ? 'fallback' : 'complete',
        failureType: message.failure_type ?? null,
        failureStage: message.failure_stage ?? null,
        failureReason: message.failure_reason ?? null,
        traceId: message.trace_id ?? null,
        processSummary: message.process_summary ?? null,
      }))
      options.chat.activeCitations.value = [...options.chat.messages.value].reverse().find((message) => message.citations?.length)?.citations ?? []
      options.chat.agentSteps.value = []
      void options.refreshContextUsage(targetSessionId)
      return true
    } catch {
      if (requestGeneration !== historyRequestGeneration) return false
      options.state.historyError.value = '会话内容暂时无法加载，请重试。'
      options.state.historyErrorSessionId.value = targetSessionId
      options.notify('error', '会话加载失败', options.state.historyError.value)
      return false
    } finally {
      if (requestGeneration === historyRequestGeneration) options.state.loadingHistory.value = false
    }
  }
  async function retryOpenSession(): Promise<boolean> {
    if (!options.state.historyErrorSessionId.value) return false
    return openSession(options.state.historyErrorSessionId.value)
  }
  async function removeSession(targetSessionId: string): Promise<void> {
    if (options.sending.value) return
    cancelTitleRefresh()
    ++sessionsGeneration
    const sessionsBeforeDelete = [...options.state.sessions.value]
    const wasActive = options.state.sessionId.value === targetSessionId
    historyRequestGeneration += 1
    options.state.loadingHistory.value = false
    options.state.historyError.value = null
    options.state.historyErrorSessionId.value = null
    try {
      await deleteSessionRequest(options.userId, targetSessionId)
      ++sessionsGeneration
      options.state.loadingSessions.value = false
      delete options.state.viewCache[targetSessionId]
      options.state.sessions.value = options.state.sessions.value.filter((item) => item.session_id !== targetSessionId)
      if (wasActive) {
        const next = nextSessionAfterDelete(sessionsBeforeDelete, targetSessionId)
        if (next) {
          const opened = await openSession(next)
          if (!opened) {
            const errorId = options.state.historyErrorSessionId.value
            const errorText = options.state.historyError.value
            resetConversation(true)
            options.state.historyErrorSessionId.value = errorId
            options.state.historyError.value = errorText
          }
        } else newSession()
      }
      options.notify('success', '会话已删除', '聊天记录与 Agent 状态已同步清除。')
    } catch (error) {
      options.notify('error', '删除会话失败', options.sessionOperationFailureDetail())
      throw error
    }
  }
  function invalidateSessions(): void { sessionsGeneration += 1 }
  return { initialized, setChatVisible, loadSessions, refreshTitleLater, cancelTitleRefresh, invalidateSessions, resetConversation, newSession, openSession, retryOpenSession, removeSession, rememberSessionMode }
}
