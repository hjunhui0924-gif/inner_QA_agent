import { ref } from 'vue'

import type { ContextUsage } from '../types/api'

export function createContextUsageState() {
  return {
    contextUsage: ref<ContextUsage | null>(null),
    contextUsageLoading: ref(false),
    contextUsageStale: ref(false),
  }
}
