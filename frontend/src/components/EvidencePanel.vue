<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import DocumentDetailDrawer from './DocumentDetailDrawer.vue'
import { fetchKnowledgeRecord, fetchSourcePreview } from '../services/api'
import type { KnowledgeRecord } from '../types/api'
import type { Citation, SourcePreviewResponse } from '../types/api'
import { copyText } from '../utils/clipboard'
import { registerOverlay } from '../utils/overlayStack'

const props = defineProps<{
  context?: string
  open: boolean
  blocked?: boolean
  modal?: boolean
  citations: Citation[]
  selectedCitationId?: string | null
  sessionId?: string | null
  turnId?: string | null
}>()

const emit = defineEmits<{ close: [] }>()
const sourceRecord = ref<KnowledgeRecord | null>(null)
const sourceOpen = ref(false)
const sourceLoading = ref(false)
const sourceError = ref('')
const sourcePreview = ref<SourcePreviewResponse | null>(null)
const sourcePreviewLoading = ref(false)
const sourcePreviewError = ref('')
const sourceCitation = ref<Citation | null>(null)
let sourceGeneration = 0
let sourceTrigger: HTMLElement | null = null
let sourceId = ''
let sourceCitationId = ''
let sourceAbort: AbortController | null = null
async function openSource(id: string, event?: Event, citationTarget?: string) {
  sourceAbort?.abort()
  const controller = new AbortController()
  sourceAbort = controller
  const sessionId = props.sessionId
  const turnId = props.turnId
  sourceId = id
  if (event) sourceTrigger = event.currentTarget as HTMLElement
  const generation = ++sourceGeneration
  sourceOpen.value = true
  sourceLoading.value = true
  sourceError.value = ''
  sourcePreview.value = null
  sourcePreviewError.value = ''
  const targetCitationId = citationTarget || props.selectedCitationId
  sourceCitationId = targetCitationId || ''
  sourceCitation.value = props.citations.find((citation) => citation.citation_id === targetCitationId && citation.source_id === id) || null
  sourcePreviewLoading.value = Boolean(sessionId && turnId && targetCitationId)
  sourceRecord.value = {
    source_id: id,
    title: '文档信息',
    source: '',
    original_filename: '',
  }
  try {
    const recordResult = await fetchKnowledgeRecord(id, controller.signal)
    if (generation !== sourceGeneration) return
    sourceRecord.value = recordResult
    if (sessionId && turnId && targetCitationId) {
      try {
        const preview = await fetchSourcePreview(id, {
          sessionId,
          turnId,
          citationId: targetCitationId,
        }, controller.signal)
        if (generation === sourceGeneration) sourcePreview.value = preview
      } catch (error) {
        if (generation === sourceGeneration) {
          const status = error && typeof error === 'object' && 'status' in error ? error.status : 0
          if (status === 401 || status === 403 || status === 404) {
            sourceRecord.value = null
            sourcePreview.value = null
            sourceError.value = '来源不存在或不可访问，请重新打开后重试。'
          } else sourcePreviewError.value = '引用原文定位暂时失败，可先查看文档内容。'
        }
      }
    }
  } catch (error) {
    const status =
      error && typeof error === 'object' && 'status' in error ? error.status : 0
    if (generation === sourceGeneration)
      sourceError.value =
        status === 403
          ? '无权访问该来源。'
          : status === 404
            ? '来源不存在或不可访问。'
            : '来源加载失败，请重试。'
  } finally {
    if (generation === sourceGeneration) sourceLoading.value = false
    if (generation === sourceGeneration) sourcePreviewLoading.value = false
  }
}
async function loadPreviewPage(cursor: string): Promise<void> {
  if (!sourceId || !props.sessionId || !props.turnId || !sourceCitationId) return
  sourceAbort?.abort()
  const controller = new AbortController()
  sourceAbort = controller
  const generation = ++sourceGeneration
  sourcePreviewLoading.value = true
  sourcePreviewError.value = ''
  try {
    const preview = await fetchSourcePreview(sourceId, {
      sessionId: props.sessionId,
      turnId: props.turnId,
      citationId: sourceCitationId,
      cursor,
    }, controller.signal)
    if (generation === sourceGeneration) sourcePreview.value = preview
  } catch (error) {
    if (generation === sourceGeneration) {
      const status = error && typeof error === 'object' && 'status' in error ? error.status : 0
      if (status === 401 || status === 403 || status === 404) {
        sourceRecord.value = null
        sourcePreview.value = null
        sourceError.value = '来源不存在或不可访问，请重新打开后重试。'
      } else sourcePreviewError.value = '原文窗口加载失败，请重试。'
    }
  } finally {
    if (generation === sourceGeneration) sourcePreviewLoading.value = false
  }
}
function closeSource() {
  ++sourceGeneration
  sourceAbort?.abort()
  sourceAbort = null
  sourceOpen.value = false
  sourceRecord.value = null
  sourcePreview.value = null
  sourceCitation.value = null
  void nextTick(() => {
    if (sourceTrigger?.isConnected) sourceTrigger.focus()
  })
}
watch(
  () => props.open,
  (open) => {
    if (!open) {
      ++sourceGeneration
      sourceAbort?.abort()
      sourceOpen.value = false
      sourceRecord.value = null
      sourcePreview.value = null
      sourceCitation.value = null
    }
  },
)
watch(() => [props.sessionId, props.turnId], () => {
  ++sourceGeneration
  sourceAbort?.abort()
  sourceOpen.value = false
  sourceRecord.value = null
  sourcePreview.value = null
  sourceCitation.value = null
})
const panel = ref<HTMLElement | null>(null)
const evidenceList = ref<HTMLElement | null>(null)
const evidenceClose = ref<HTMLButtonElement | null>(null)
const copyFeedback = ref<Record<string, 'copied' | 'failed'>>({})
const titleId = `evidence-title-${useId()}`
const verificationLabels: Record<string, string> = {
  provenance_only: '已核对引用出处',
  grounded: '已校验答案依据',
  verified: '已校验引用',
  web_source: '搜索服务提供的来源',
}
function sourceUrl(value?: string): string | undefined {
  if (!value || /\s/.test(value)) return undefined
  try {
    const url = new URL(value)
    return ['https:', 'http:'].includes(url.protocol) &&
      !url.username &&
      !url.password
      ? url.href
      : undefined
  } catch {
    return undefined
  }
}
const timers: number[] = []
let overlay: ReturnType<typeof registerOverlay> | null = null

function citationElement(
  citationId: string | null | undefined,
): HTMLElement | null {
  if (!citationId || !evidenceList.value) return null
  return (
    Array.from(
      evidenceList.value.querySelectorAll<HTMLElement>('[data-citation-id]'),
    ).find((element) => element.dataset.citationId === citationId) ?? null
  )
}

async function focusSelectedCitation(): Promise<void> {
  await nextTick()
  const target = citationElement(props.selectedCitationId)
  if (!target) {
    evidenceClose.value?.focus()
    return
  }
  const reduceMotion =
    window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
  target.focus({ preventScroll: true })
  const list = evidenceList.value
  if (!list) return
  const targetTop = Math.max(0, target.offsetTop - 20)
  if (typeof list.scrollTo === 'function') {
    list.scrollTo({
      top: targetTop,
      behavior: reduceMotion ? 'auto' : 'smooth',
    })
  } else {
    list.scrollTop = targetTop
  }
  target.classList.add('selected')
  timers.push(
    window.setTimeout(() => target.classList.remove('selected'), 1000),
  )
}

function trapFocus(event: KeyboardEvent): void {
  const focusable = panel.value?.querySelectorAll<HTMLElement>(
    'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
  )
  if (!focusable?.length) {
    event.preventDefault()
    panel.value?.focus()
    return
  }
  const first = focusable[0]
  const last = focusable[focusable.length - 1]
  if (!panel.value?.contains(document.activeElement)) {
    event.preventDefault()
    first.focus()
    return
  }
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first.focus()
  }
}

function handleKeydown(event: KeyboardEvent): void {
  if (!props.open) return
  if (event.key === 'Escape') {
    event.preventDefault()
    emit('close')
    return
  }
  if (props.modal && event.key === 'Tab') trapFocus(event)
}

watch(
  () => [props.open, props.selectedCitationId, props.citations.length],
  () => {
    if (props.open) void focusSelectedCitation()
  },
  { flush: 'post' },
)

onMounted(() => {
  overlay = registerOverlay(handleKeydown, { modal: props.modal })
  overlay.setOpen(props.open)
})
onBeforeUnmount(() => {
  ++sourceGeneration
  sourceAbort?.abort()
  timers.forEach(clearTimeout)
  overlay?.unregister()
  overlay = null
})

watch(
  () => [props.open, props.modal],
  ([open, modal]) => {
    overlay?.setModal(Boolean(modal))
    overlay?.setOpen(Boolean(open))
  },
)

async function copyQuote(citation: Citation, event: Event): Promise<void> {
  void event
  try {
    await copyText(citation.quote)
    copyFeedback.value = {
      ...copyFeedback.value,
      [citation.citation_id]: 'copied',
    }
    timers.push(
      window.setTimeout(() => {
        const next = { ...copyFeedback.value }
        delete next[citation.citation_id]
        copyFeedback.value = next
      }, 1400),
    )
  } catch {
    copyFeedback.value = {
      ...copyFeedback.value,
      [citation.citation_id]: 'failed',
    }
    timers.push(
      window.setTimeout(() => {
        const next = { ...copyFeedback.value }
        delete next[citation.citation_id]
        copyFeedback.value = next
      }, 1400),
    )
  }
}
</script>

<template>
  <aside
    ref="panel"
    class="evidence-panel"
    :class="{ open }"
    :role="modal ? 'dialog' : 'complementary'"
    :aria-modal="modal ? 'true' : undefined"
    :aria-labelledby="titleId"
    aria-label="引用证据"
    :aria-hidden="!open || blocked ? 'true' : undefined"
    :inert="!open || sourceOpen || blocked ? true : undefined"
    tabindex="-1"
  >
    <div class="evidence-header">
      <div>
        <p class="eyebrow">回到知识的出处</p>
        <h2 :id="titleId">引用来源</h2>
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

    <p v-if="context" class="evidence-note">{{ context }}</p>
    <div v-if="citations.length === 0" class="evidence-empty">
      <span class="evidence-number">[1]</span>
      <h3>回答后查看来源</h3>
      <p>在这里查看文档原文、所在位置或网页来源，核对回答依据。</p>
    </div>

    <div v-else ref="evidenceList" class="evidence-list">
      <article
        v-for="(citation, index) in citations"
        :key="citation.citation_id"
        class="evidence-card"
        :data-citation-id="citation.citation_id"
        :id="`evidence-${citation.citation_id}`"
        tabindex="-1"
      >
        <div class="evidence-card-heading">
          <span class="evidence-number">[{{ index + 1 }}]</span>
          <div>
            <h3>{{ citation.title || '未命名文档' }}</h3>
            <p>
              <span v-if="citation.page">第 {{ citation.page }} 页</span>
              <span v-if="citation.section">{{ citation.section }}</span>
              <span v-if="citation.filename">{{ citation.filename }}</span>
            </p>
          </div>
        </div>
        <blockquote v-if="citation.quote">{{ citation.quote }}</blockquote>
        <a
          v-if="sourceUrl(citation.url)"
          :href="sourceUrl(citation.url)"
          target="_blank"
          rel="noopener noreferrer"
          class="text-button"
          >打开网页来源</a
        >
        <button
          v-if="citation.source_id"
          type="button"
          class="text-button"
          @click="openSource(citation.source_id, $event, citation.citation_id)"
        >
          查看文档信息与下载
        </button>
        <div class="evidence-card-footer">
          <span class="verification-label">{{
            verificationLabels[citation.verification_status || ''] ||
            '请核对引用原文'
          }}</span>
          <button
            v-if="citation.quote"
            class="text-button copy-quote"
            type="button"
            @click="copyQuote(citation, $event)"
          >
            {{
              copyFeedback[citation.citation_id] === 'copied'
                ? '已复制'
                : copyFeedback[citation.citation_id] === 'failed'
                  ? '复制失败'
                  : '复制原文'
            }}
          </button>
        </div>
      </article>
    </div>

    <div class="evidence-note">
      引用帮助你查阅来源。来源可追溯不代表已核实回答中的所有结论。
    </div>
  </aside>
  <DocumentDetailDrawer
    :open="sourceOpen"
    :record="sourceRecord"
    :loading="sourceLoading"
    :error="sourceError"
    :preview="sourcePreview"
    :preview-loading="sourcePreviewLoading"
    :preview-error="sourcePreviewError"
    :citation-page="sourceCitation?.page"
    :citation-quote="sourceCitation?.quote"
    @preview-page="loadPreviewPage"
    @preview-return="openSource(sourceId, undefined, sourceCitationId)"
    @close="closeSource"
    @retry="openSource(sourceId, undefined, sourceCitationId)"
  />
</template>
