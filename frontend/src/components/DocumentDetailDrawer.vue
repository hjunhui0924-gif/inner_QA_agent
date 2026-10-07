<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { downloadKnowledge } from '../services/api'
import type { KnowledgeRecord, SourcePreviewResponse } from '../types/api'
import {
  departmentLabel,
  sourceLabel,
  formatKnowledgeDateRange,
  knowledgeStatusLabel,
  knowledgeStatusTone,
} from '../utils/knowledge'
import { registerOverlay } from '../utils/overlayStack'
import PdfSourcePreview from './PdfSourcePreview.vue'

const props = defineProps<{
  open: boolean
  record: KnowledgeRecord | null
  loading?: boolean
  error?: string
  preview?: SourcePreviewResponse | null
  previewLoading?: boolean
  previewError?: string
  citationPage?: number | null
  citationQuote?: string
}>()

const emit = defineEmits<{ close: []; retry: []; previewPage: [cursor: string]; previewReturn: [] }>()
const drawer = ref<HTMLElement | null>(null)
const closeButton = ref<HTMLButtonElement | null>(null)
const downloading = ref(false)
const pdfOpen = ref(false)
const downloadError = ref('')
function previewParts(block: { id: string; text: string }): Array<{ text: string; marked: boolean }> {
  const location = props.preview?.location
  if (!location || location.block_id !== block.id || location.start === null || location.end === null)
    return [{ text: block.text, marked: false }]
  const points = Array.from(block.text)
  const start = Math.max(0, Math.min(points.length, location.start))
  const end = Math.max(start, Math.min(points.length, location.end))
  return [
    { text: points.slice(0, start).join(''), marked: false },
    { text: points.slice(start, end).join(''), marked: true },
    { text: points.slice(end).join(''), marked: false },
  ].filter((part) => part.text)
}
let downloadGeneration = 0
watch(() => [props.open, props.record?.source_id], () => { ++downloadGeneration; downloading.value = false; downloadError.value = ''; pdfOpen.value = false })
watch(() => props.preview, async (preview) => {
  if (!props.open || !preview?.location.block_id || pdfOpen.value) return
  await nextTick()
  if (!props.open || props.preview !== preview) return
  const target = [...(drawer.value?.querySelectorAll<HTMLElement>('.source-preview-block') || [])]
    .find((block) => block.dataset.blockId === preview.location.block_id)
  target?.scrollIntoView?.({ block: 'nearest', behavior: 'instant' })
})
let overlay: ReturnType<typeof registerOverlay> | null = null

function closeOnEscape(event: KeyboardEvent): void {
  if (!props.open) return
  if (event.key === 'Escape') {
    event.preventDefault()
    emit('close')
    return
  }
  if (event.key === 'Tab') trapFocus(event)
}

function trapFocus(event: KeyboardEvent): void {
  const focusable = drawer.value?.querySelectorAll<HTMLElement>(
    'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
  )
  if (!focusable?.length) {
    event.preventDefault()
    drawer.value?.focus()
    return
  }
  const first = focusable[0]
  const last = focusable[focusable.length - 1]
  if (!drawer.value?.contains(document.activeElement)) {
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

async function downloadRecord(): Promise<void> {
  const sourceId = props.record?.source_id
  if (!sourceId || downloading.value) return
  const generation = ++downloadGeneration
  const filename = props.record?.original_filename || `${sourceId}.bin`
  downloading.value = true
  downloadError.value = ''
  try {
    const blob = await downloadKnowledge(sourceId)
    if (generation !== downloadGeneration || !props.open) return
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
  } catch (error) {
    const status = error && typeof error === 'object' && 'status' in error ? error.status : 0
    if (generation === downloadGeneration) downloadError.value = status === 403 ? '无权下载该来源。' : status === 404 ? '来源文件不存在或不可访问。' : '来源下载失败，请稍后重试。'
  } finally {
    if (generation === downloadGeneration) downloading.value = false
  }
}

watch(
  () => props.open,
  (open) => {
    if (open) void nextTick(() => closeButton.value?.focus())
  },
)

onMounted(() => {
  overlay = registerOverlay(closeOnEscape, { modal: true })
  overlay.setOpen(props.open)
})
onBeforeUnmount(() => {
  ++downloadGeneration
  overlay?.unregister()
  overlay = null
})

watch(
  () => props.open,
  (open) => overlay?.setOpen(open),
)
</script>

<template>
  <div class="detail-drawer-shell" :class="{ open }">
    <div class="detail-drawer-scrim" :class="{ visible: open }" @click="emit('close')" />
    <aside
      ref="drawer"
      class="document-detail-drawer"
      :class="{ open }"
      role="dialog"
      aria-modal="true"
      aria-labelledby="document-detail-title"
      aria-label="文档详情"
      :aria-hidden="!open ? 'true' : undefined"
      :inert="!open ? true : undefined"
      tabindex="-1"
    >
      <header class="detail-drawer-header">
        <div>
          <p class="eyebrow">了解这份资料</p>
          <h2 id="document-detail-title">文档详情</h2>
        </div>
        <button
          ref="closeButton"
          class="text-button"
          type="button"
          aria-label="关闭文档详情"
          @click="emit('close')"
        >
          关闭
        </button>
      </header>

      <div v-if="record" class="detail-drawer-body">
        <p class="source-version-note">以下为当前可访问文档信息，不代表历史回答使用时的版本快照。</p>
        <div class="detail-title-block">
          <span class="document-type-mark">DOC</span>
          <div>
            <h3>{{ record.title || record.original_filename || '未命名文档' }}</h3>
            <p>{{ record.original_filename || '原始文件名未记录' }}</p>
          </div>
        </div>

        <div class="detail-status-line">
          <span class="status-badge" :class="`status-${knowledgeStatusTone(record.status)}`">
            {{ knowledgeStatusLabel(record.status) }}
          </span>
          <span v-if="record.source_id" class="detail-code">{{ record.source_id }}</span>
        </div>

        <div class="detail-actions">
          <button
            class="secondary-button"
            type="button"
            :disabled="!record.source_id || !record.original_filename || preview?.original_available === false || downloading"
            @click="downloadRecord"
          >
            {{ downloading ? '准备下载…' : '下载原文件' }}
          </button>
          <p v-if="downloadError" class="field-error" role="alert">{{ downloadError }}</p>
          <p v-if="preview?.original_available === false" class="field-error">原文件不存在或不可访问，可查看抽取文本。</p>
        </div>
        <button
          v-if="record.original_filename?.toLowerCase().endsWith('.pdf')"
          class="secondary-button"
          type="button"
          :disabled="preview?.original_available === false"
          @click="pdfOpen = !pdfOpen"
        >
          {{ pdfOpen ? '收起 PDF 原页' : '打开 PDF 原页' }}
        </button>
        <PdfSourcePreview v-if="open && pdfOpen && record.source_id" :source-id="record.source_id" :open="open && pdfOpen" :initial-page="citationPage ?? preview?.location.page" :initial-quote="citationQuote" />

        <dl class="detail-fields">
          <div>
            <dt>来源</dt>
            <dd>{{ sourceLabel(record.source) }}</dd>
          </div>
          <div>
            <dt>来源类型</dt>
            <dd>{{ sourceLabel(record.source_type) }}</dd>
          </div>
          <div>
            <dt>部门</dt>
            <dd>{{ departmentLabel(record.department) }}</dd>
          </div>
          <div>
            <dt>版本</dt>
            <dd>{{ record.version || '未记录' }}</dd>
          </div>
          <div>
            <dt>负责人</dt>
            <dd>{{ record.owner || '未指定' }}</dd>
          </div>
          <div>
            <dt>访问范围</dt>
            <dd>
              {{
                record.access_scope === 'internal' ? '企业内部' : record.access_scope || '未记录'
              }}
            </dd>
          </div>
          <div>
            <dt>生效期间</dt>
            <dd>{{ formatKnowledgeDateRange(record.effective_from, record.effective_to, record.effective_from_provided) }}</dd>
          </div>
          <div v-if="record.content_length !== undefined">
            <dt>内容字数</dt>
            <dd>{{ Number(record.content_length).toLocaleString() }} 字</dd>
          </div>
        </dl>

        <section class="detail-preview" aria-labelledby="preview-title">
          <div class="detail-section-heading">
            <h3 id="preview-title">内容预览</h3>
            <span>原文内容</span>
          </div>
          <p v-if="previewLoading" role="status">正在定位引用原文…</p>
          <p v-else-if="loading" role="status">正在加载文档内容…</p>
          <div v-else-if="error" role="alert">
            <p class="field-error">{{ error }}</p>
            <button class="secondary-button" type="button" @click="emit('retry')">重新加载</button>
          </div>
          <div v-else-if="preview" class="source-preview-body">
            <p v-if="preview.revision_match === 'changed'" role="status">当前文档与该回答引用时的版本不同，请核对更新后的原文。</p>
            <p v-else-if="preview.revision_match === 'unknown'">该引用未记录版本校验信息，无法确认是否与当前文档一致。</p>
            <p v-if="previewError" class="field-error" role="alert">{{ previewError }}</p>
            <p v-if="preview.location.status === 'exact'">已定位到引用原文。</p>
            <p v-else-if="preview.location.status === 'normalized'">已按空白归一化定位，可能与当前排版略有差异。</p>
            <p v-else-if="preview.location.status === 'ambiguous'">找到多处相似内容，请手动核对。</p>
            <p v-else-if="preview.location.status === 'not_found'">当前文档未找到该引用摘录。</p>
            <p v-else>当前仅提供文档内容预览。</p>
            <button v-if="preview.location.status !== 'unavailable'" type="button" class="text-button" :disabled="previewLoading" @click="emit('previewReturn')">回到引用位置</button>
            <article v-for="block in preview.blocks" :key="block.id" :data-block-id="block.id" class="source-preview-block">
              <header>
                <span v-if="block.page">第 {{ block.page }} 页</span>
                <span v-if="block.section">{{ block.section }}</span>
              </header>
              <p><template v-for="part in previewParts(block)" :key="`${block.id}-${part.text}-${part.marked}`"><mark v-if="part.marked">{{ part.text }}</mark><template v-else>{{ part.text }}</template></template></p>
            </article>
            <nav v-if="preview.previous_cursor || preview.next_cursor" class="source-preview-navigation" aria-label="原文窗口导航">
              <button v-if="preview.previous_cursor" type="button" class="text-button" @click="emit('previewPage', preview.previous_cursor)">上一窗口</button>
              <button v-if="preview.next_cursor" type="button" class="text-button" @click="emit('previewPage', preview.next_cursor)">下一窗口</button>
            </nav>
          </div>
          <div v-else-if="previewError" role="alert">
            <p class="field-error">{{ previewError }}</p>
          </div>
          <p v-else>{{ record.content || record.preview || '暂无预览，可下载原文件查看。' }}</p>
        </section>

        <section class="detail-integrity" aria-labelledby="integrity-title">
          <div class="detail-section-heading">
            <h3 id="integrity-title">内容校验</h3>
            <span>只读信息</span>
          </div>
          <dl>
            <div v-if="record.content_checksum">
              <dt>Checksum</dt>
              <dd>{{ record.content_checksum }}</dd>
            </div>
            <div v-if="record.content_fingerprint">
              <dt>Fingerprint</dt>
              <dd>{{ record.content_fingerprint }}</dd>
            </div>
            <div v-if="!record.content_checksum && !record.content_fingerprint">
              <dd>当前接口未返回校验摘要。</dd>
            </div>
          </dl>
        </section>

        <p class="detail-drawer-note">仅可下载你有权访问的资料。</p>
      </div>
      <div v-else class="detail-drawer-body">
        <p v-if="loading" role="status">正在加载文档内容…</p>
        <div v-else-if="error" role="alert">
          <p class="field-error">{{ error }}</p>
          <button type="button" class="secondary-button" @click="emit('retry')">重新加载</button>
        </div>
      </div>
    </aside>
  </div>
</template>
