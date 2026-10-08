import { ref } from 'vue'

import type { AgentStep, Citation, ChatMode, UiMessage } from '../types/api'

export function createChatState() {
  return {
    messages: ref<UiMessage[]>([]),
    agentSteps: ref<AgentStep[]>([]),
    activeCitations: ref<Citation[]>([]),
    backendOnline: ref(false),
    sending: ref(false),
    sendingStartedAt: ref(0),
    chatMode: ref<ChatMode>('knowledge'),
    webSearchEnabled: ref(false),
  }
}
