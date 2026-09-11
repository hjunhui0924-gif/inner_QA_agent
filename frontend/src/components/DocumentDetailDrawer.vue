<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import type { KnowledgeRecord } from '../types/api'
import { formatKnowledgeDateRange, knowledgeStatusLabel, knowledgeStatusTone } from '../utils/knowledge'

const props = defineProps<{
  open: boolean
  record: KnowledgeRecord | null
}>()

const emit = defineEmits<{ close: [] }>()
const closeButton = ref<HTMLButtonElement | null>(null)

function closeOnEscape(event: KeyboardEvent): void {
  if (event.key === 'Escape' && props.open) emit('close')
}

watch(
  () => props.open,
  (open) => {
    if (open) void nextTick(() => closeButton.value?.focus())
  },
)

onMounted(() => window.addEventListener('keydown', closeOnEscape))
onBeforeUnmount(() => window.removeEventListener('keydown', closeOnEscape))
</script>

<template>
  <div class="detail-drawer-shell" :class="{ open }">
    <div class="detail-drawer-scrim" :class="{ visible: open }" @click="emit('close')" />
    <aside
      class="document-detail-drawer"
      :class="{ open }"
      role="dialog"
      aria-modal="true"
      aria-labelledby="document-detail-title"
      aria-label="文档详情"
      :aria-hidden="!open"
      :inert="!open"
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
