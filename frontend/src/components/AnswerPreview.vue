<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'

import { renderMarkdown } from '../utils/markdown'
import { recordMarkdownRender } from '../utils/performance'

const props = defineProps<{ content: string; label: string }>()
const previewHtml = ref('')
let previewTimer: ReturnType<typeof setTimeout> | undefined
let cachedContent = ''
let cachedHtml = ''

function flush(): void {
  previewTimer = undefined
  if (!props.content) {
    previewHtml.value = ''
    cachedContent = ''
    cachedHtml = ''
    return
  }
  if (cachedContent !== props.content) {
    const started = performance.now()
    cachedHtml = renderMarkdown(props.content, [])
    recordMarkdownRender(performance.now() - started)
    cachedContent = props.content
  }
  previewHtml.value = cachedHtml
}

watch(
  () => props.content,
  () => {
    if (previewTimer === undefined) previewTimer = setTimeout(flush, 50)
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  if (previewTimer !== undefined) clearTimeout(previewTimer)
})
</script>

<template>
  <div class="answer-preview">
    <small class="preview-label">{{ label }}</small>
    <div class="message-content" v-html="previewHtml" />
  </div>
</template>
