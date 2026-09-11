<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import type { KnowledgeRecord } from '../types/api'
import { formatKnowledgeDateRange, knowledgeStatusLabel, knowledgeStatusTone } from '../utils/knowledge'
import { registerOverlay } from '../utils/overlayStack'

const props = defineProps<{
  open: boolean
  record: KnowledgeRecord | null
}>()

const emit = defineEmits<{ close: [] }>()
const drawer = ref<HTMLElement | null>(null)
const closeButton = ref<HTMLButtonElement | null>(null)
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
          <p class="eyebrow">DOCUMENT RECORD</p>
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

        <dl class="detail-fields">
          <div><dt>来源</dt><dd>{{ record.source || '未记录' }}</dd></div>
          <div><dt>来源类型</dt><dd>{{ record.source_type || '未记录' }}</dd></div>
          <div><dt>部门</dt><dd>{{ record.department || '未指定' }}</dd></div>
          <div><dt>版本</dt><dd>{{ record.version || '未记录' }}</dd></div>
          <div><dt>负责人</dt><dd>{{ record.owner || '未指定' }}</dd></div>
          <div><dt>访问范围</dt><dd>{{ record.access_scope || '未记录' }}</dd></div>
          <div><dt>生效期间</dt><dd>{{ formatKnowledgeDateRange(record.effective_from, record.effective_to) }}</dd></div>
          <div v-if="record.content_length !== undefined"><dt>内容字数</dt><dd>{{ Number(record.content_length).toLocaleString() }} 字</dd></div>
        </dl>

        <section class="detail-preview" aria-labelledby="preview-title">
          <div class="detail-section-heading">
            <h3 id="preview-title">内容预览</h3>
            <span>接口返回摘要</span>
          </div>
          <p>{{ record.preview || record.content || '当前接口未返回内容预览。' }}</p>
        </section>

        <section class="detail-integrity" aria-labelledby="integrity-title">
          <div class="detail-section-heading">
            <h3 id="integrity-title">内容校验</h3>
            <span>只读信息</span>
          </div>
          <dl>
            <div v-if="record.content_checksum"><dt>Checksum</dt><dd>{{ record.content_checksum }}</dd></div>
            <div v-if="record.content_fingerprint"><dt>Fingerprint</dt><dd>{{ record.content_fingerprint }}</dd></div>
            <div v-if="!record.content_checksum && !record.content_fingerprint"><dd>当前接口未返回校验摘要。</dd></div>
          </dl>
        </section>

        <p class="detail-drawer-note">
          当前版本仅提供文档元数据和内容预览；下载、删除及解析阶段进度需要后端接口支持。
        </p>
      </div>
    </aside>
  </div>
</template>
