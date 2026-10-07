<script setup lang="ts">
import { computed, ref } from 'vue'
import type { ContextUsage } from '../types/api'

const props = defineProps<{
  usage: ContextUsage | null
  loading?: boolean
  stale?: boolean
}>()

const pinned = ref(false)
const hovered = ref(false)
const visible = computed(() => pinned.value || hovered.value)
const percent = computed(() => {
  if (!props.usage || props.usage.status !== 'available' || props.usage.used_tokens === null)
    return 0
  return Math.min(100, Math.max(0, (props.usage.used_tokens / props.usage.budget_tokens) * 100))
})
const label = computed(() => {
  if (!props.usage || props.usage.status !== 'available') return '用量暂不可用'
  return `估算 ${props.usage.used_tokens} / ${props.usage.budget_tokens} tokens`
})
function toggle(): void { pinned.value = !pinned.value }
</script>

<template>
  <div class="context-meter" @mouseenter="hovered = true" @mouseleave="hovered = false">
    <button
      class="context-meter-trigger"
      type="button"
      aria-label="会话上下文预算"
      :aria-expanded="pinned"
      @click="toggle"
      @keydown.esc="pinned = false"
      @focus="hovered = true"
      @blur="hovered = false"
    >
      <svg viewBox="0 0 36 36" aria-hidden="true">
        <circle class="context-meter-track" cx="18" cy="18" r="15" />
        <circle class="context-meter-value" cx="18" cy="18" r="15" :style="{ strokeDasharray: `${percent} 100` }" />
      </svg>
      <span>上下文</span>
    </button>
    <div v-if="visible" class="context-meter-popover" role="status">
      <strong>{{ label }}</strong>
      <span v-if="loading">更新中…</span>
      <span v-else-if="stale">数据暂未更新</span>
      <span v-else>仅统计已保存的会话历史与摘要。</span>
    </div>
  </div>
</template>
