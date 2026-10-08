import { computed, reactive, ref, type ComputedRef, type Ref } from 'vue'

import type { ChatMode, SessionSummary } from '../types/api'
import type { ConversationViewState } from '../types/conversation'

export function createSessionState(generateId: () => string) {
  const sessionId = ref(generateId())
  const sessions = ref<SessionSummary[]>([])
  const viewCache = reactive<Record<string, ConversationViewState>>({})
  const currentView = computed(() => {
    if (!viewCache[sessionId.value]) viewCache[sessionId.value] = { draft: '', scrollTop: 0 }
    return viewCache[sessionId.value]!
  })
  const draft = computed({
    get: () => currentView.value.draft,
    set: (value: string) => { currentView.value.draft = value },
  })
  const loadingSessions = ref(false)
  const sessionsError = ref<string | null>(null)
  const loadingHistory = ref(false)
  const historyError = ref<string | null>(null)
  const historyErrorSessionId = ref<string | null>(null)
  const sessionTitlePending = ref(false)
  const chatVisible = ref(false)

  return {
    sessionId,
    sessions,
    viewCache,
    currentView,
    draft,
    loadingSessions,
    sessionsError,
    loadingHistory,
    historyError,
    historyErrorSessionId,
    sessionTitlePending,
    chatVisible,
  }
}

export type SessionState = ReturnType<typeof createSessionState>
