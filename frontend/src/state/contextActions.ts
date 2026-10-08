import type { Ref } from 'vue'

import { fetchContextUsage } from '../services/api'
import type { ContextUsage } from '../types/api'
import type { createContextUsageState } from './contextUsageState'

interface ContextActionOptions {
  userId: string
  sessionId: Ref<string>
  state: ReturnType<typeof createContextUsageState>
}

export function createContextActions(options: ContextActionOptions) {
  let generation = 0

  async function refreshContextUsage(targetSessionId = options.sessionId.value): Promise<void> {
    const requestGeneration = ++generation
    options.state.contextUsageLoading.value = true
    try {
      const usage = await fetchContextUsage(options.userId, targetSessionId)
      if (requestGeneration !== generation || targetSessionId !== options.sessionId.value) return
      options.state.contextUsage.value = usage
      options.state.contextUsageStale.value = usage.status === 'unavailable'
    } catch {
      if (requestGeneration === generation && targetSessionId === options.sessionId.value)
        options.state.contextUsageStale.value = true
    } finally {
      if (requestGeneration === generation) options.state.contextUsageLoading.value = false
    }
  }

  return { refreshContextUsage }
}
