<script setup lang="ts">
import { computed, ref } from 'vue'

import { useWorkspace } from '../composables/useWorkspace'

const {
  knowledgeRecords,
  loadingKnowledge,
  uploading,
  loadKnowledge,
  uploadKnowledge,
} = useWorkspace()
const title = ref('')
const source = ref('internal_upload')
const selectedFile = ref<File | null>(null)
const dragActive = ref(false)

const fileSummary = computed(() => {
  if (!selectedFile.value) return 'PDF、DOCX、Markdown、TXT 等，单个文件不超过 20MB'
  const megabytes = selectedFile.value.size / (1024 * 1024)
  return `${megabytes.toFixed(2)} MB`
})

function setFile(file?: File) {
  selectedFile.value = file ?? null
}

function chooseFile(event: Event) {
  const input = event.target as HTMLInputElement
  setFile(input.files?.[0])
}

function dropFile(event: DragEvent) {
  dragActive.value = false
  setFile(event.dataTransfer?.files?.[0])
}

async function submitUpload() {
  if (!selectedFile.value || uploading.value) return
  try {
    await uploadKnowledge(selectedFile.value, title.value.trim(), source.value.trim())
    title.value = ''
    selectedFile.value = null
  } catch {
    // The workspace reports upload errors through its toast stack.
  }
}

function displayName(record: (typeof knowledgeRecords.value)[number]): string {
  return record.title || record.original_filename || '未命名文档'
}
</script>

<template>
  <section class="knowledge-page">
    <header class="workspace-header knowledge-header">
      <div>
        <p class="eyebrow">KNOWLEDGE OPERATIONS</p>
        <h1>知识库</h1>
      </div>
      <div class="header-note">{{ knowledgeRecords.length }} 份已入库文档</div>
    </header>

    <div class="knowledge-grid">
      <section class="upload-panel">
        <span class="chapter-mark">01 / INGEST</span>
        <h2>添加内部文档</h2>
        <p>上传后将自动解析、切块、去重并写入检索索引。</p>

        <div class="form-grid">
          <label>
            <span>知识标题</span>
            <input v-model="title" type="text" placeholder="留空时使用文件名" :disabled="uploading" />
          </label>
          <label>
            <span>来源标签</span>
            <input v-model="source" type="text" :disabled="uploading" />
          </label>
        </div>

        <label
          class="drop-zone"
          :class="{ active: dragActive }"
          @dragenter.prevent="dragActive = true"
          @dragover.prevent="dragActive = true"
          @dragleave.prevent="dragActive = false"
          @drop.prevent="dropFile"
        >
          <input
            type="file"
            accept=".txt,.md,.csv,.json,.pdf,.docx,.py,.log"
            :disabled="uploading"
            @change="chooseFile"
          />
          <span class="drop-index">DOC</span>
          <strong>{{ selectedFile?.name ?? '选择或拖入企业文档' }}</strong>
          <span>{{ fileSummary }}</span>
        </label>

        <button
          class="primary-button upload-button"
          type="button"
          :disabled="!selectedFile || uploading"
          @click="submitUpload"
        >
          {{ uploading ? '正在解析并建立索引…' : '上传并建立索引' }}
        </button>
      </section>

      <section class="library-panel">
        <div class="library-heading">
          <div>
            <span class="chapter-mark">02 / LIBRARY</span>
            <h2>已入库文件</h2>
          </div>
          <button class="secondary-button" type="button" :disabled="loadingKnowledge" @click="loadKnowledge">
            {{ loadingKnowledge ? '刷新中…' : '刷新列表' }}
          </button>
        </div>

        <div v-if="loadingKnowledge" class="library-empty compact">
          <span class="loading-ring" />
          <h3>正在读取知识库</h3>
        </div>
        <div v-else-if="knowledgeRecords.length === 0" class="library-empty">
          <span class="empty-rule" />
          <h3>知识库还是空的</h3>
          <p>上传第一份制度、流程或规范文档后，即可开始可追溯问答。</p>
        </div>
        <div v-else class="document-list">
          <article v-for="(record, index) in knowledgeRecords" :key="`${displayName(record)}-${index}`" class="document-row">
            <span class="document-index">{{ String(index + 1).padStart(2, '0') }}</span>
            <div class="document-main">
              <h3>{{ displayName(record) }}</h3>
              <p>{{ record.original_filename || '原始文件名未记录' }}</p>
            </div>
            <div class="document-meta">
              <span>{{ record.source || 'unknown' }}</span>
              <span v-if="record.content?.length">{{ record.content.length.toLocaleString() }} 字</span>
            </div>
          </article>
        </div>
      </section>
    </div>
  </section>
</template>
