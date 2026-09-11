<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import type { Citation } from '../types/api'

const props = defineProps<{
  open: boolean
  citations: Citation[]
  selectedCitationId?: string | null
}>()

const emit = defineEmits<{ close: [] }>()
const evidenceList = ref<HTMLElement | null>(null)
const evidenceClose = ref<HTMLButtonElement | null>(null)
const copyFeedback = ref<Record<string, 'copied' | 'failed'>>({})

function citationElement(citationId: string | null | undefined): HTMLElement | null {
  if (!citationId || !evidenceList.value) return null
  return Array.from(
    evidenceList.value.querySelectorAll<HTMLElement>('[data-citation-id]'),
  ).find((element) => element.dataset.citationId === citationId) ?? null
}

async function focusSelectedCitation(): Promise<void> {
  await nextTick()
  const target = citationElement(props.selectedCitationId)
  if (!target) {
    evidenceClose.value?.focus()
    return
  }
  const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
  target.focus({ preventScroll: true })
  target.scrollIntoView({ block: 'nearest', behavior: reduceMotion ? 'auto' : 'smooth' })
  target.classList.add('selected')
  window.setTimeout(() => target.classList.remove('selected'), 1600)
}

watch(
  () => [props.open, props.selectedCitationId, props.citations.length],
  () => {
    if (props.open) void focusSelectedCitation()
  },
  { flush: 'post' },
)

async function copyQuote(citation: Citation, event: Event): Promise<void> {
  void event
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(citation.quote)
    } else {
      const textarea = document.createElement('textarea')
      textarea.value = citation.quote
      textarea.setAttribute('readonly', '')
      textarea.style.position = 'fixed'
      textarea.style.opacity = '0'
      document.body.appendChild(textarea)
      textarea.select()
      const copied = document.execCommand('copy')
      textarea.remove()
      if (!copied) throw new Error('copy failed')
    }
    copyFeedback.value = { ...copyFeedback.value, [citation.citation_id]: 'copied' }
    window.setTimeout(() => {
      const next = { ...copyFeedback.value }
      delete next[citation.citation_id]
      copyFeedback.value = next
    }, 1400)
  } catch {
    copyFeedback.value = { ...copyFeedback.value, [citation.citation_id]: 'failed' }
    window.setTimeout(() => {
      const next = { ...copyFeedback.value }
      delete next[citation.citation_id]
      copyFeedback.value = next
    }, 1400)
  }
}
</script>

<template>
  <aside
    class="evidence-panel"
    :class="{ open }"
    aria-label="引用证据"
    :aria-hidden="!open"
    :inert="!open"
  >
    <div class="evidence-header">
      <div>
        <p class="eyebrow">VERIFIABLE ANSWERS</p>
        <h2>引用证据</h2>
      </div>
      <button
        ref="evidenceClose"
        class="text-button evidence-close"
        type="button"
        aria-label="关闭引用证据"
        @click="emit('close')"
      >
        收起
      </button>
    </div>

    <div v-if="citations.length === 0" class="evidence-empty">
      <span class="evidence-number">C1</span>
      <h3>回答后查看原文</h3>
      <p>引用文件、页码、章节与逐字原文将在这里展开，帮助你核对回答依据。</p>
    </div>

    <div v-else ref="evidenceList" class="evidence-list">
      <article
        v-for="citation in citations"
        :key="citation.citation_id"
        class="evidence-card"
        :data-citation-id="citation.citation_id"
        :id="`evidence-${citation.citation_id}`"
        tabindex="-1"
      >
        <div class="evidence-card-heading">
          <span class="evidence-number">{{ citation.citation_id }}</span>
          <div>
            <h3>{{ citation.title || '未命名文档' }}</h3>
            <p>
              <span v-if="citation.page">第 {{ citation.page }} 页</span>
              <span v-if="citation.section">{{ citation.section }}</span>
              <span v-if="citation.source">{{ citation.source }}</span>
              <span v-if="citation.filename">{{ citation.filename }}</span>
              <span v-if="citation.chunk_id">{{ citation.chunk_id }}</span>
            </p>
          </div>
        </div>
        <blockquote>{{ citation.quote }}</blockquote>
        <div class="evidence-card-footer">
          <span class="verification-label">{{ citation.verification_status || 'provenance_only' }}</span>
          <button class="text-button copy-quote" type="button" @click="copyQuote(citation, $event)">
            {{ copyFeedback[citation.citation_id] === 'copied'
              ? '已复制'
              : copyFeedback[citation.citation_id] === 'failed'
                ? '复制失败'
                : '复制原文' }}
          </button>
        </div>
      </article>
    </div>

    <div class="evidence-note">
      出处用于定位原文；回答是否受证据支持，由后端校验流程独立判断。
    </div>
  </aside>
</template>
