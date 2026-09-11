<script setup lang="ts">
import { computed, nextTick, reactive, ref } from 'vue'
import { Close, DocumentAdd, Refresh, Search } from '@element-plus/icons-vue'

import DocumentDetailDrawer from '../components/DocumentDetailDrawer.vue'
import { useWorkspace } from '../composables/useWorkspace'
import type { KnowledgeRecord } from '../types/api'
import {
  filterKnowledgeRecords,
  formatFileSize,
  formatKnowledgeDateRange,
  KNOWLEDGE_FILE_ACCEPT,
  knowledgeStatusLabel,
  knowledgeStatusTone,
  readableKnowledgeUploadError,
  uniqueKnowledgeValues,
  validateKnowledgeFile,
} from '../utils/knowledge'

const {
  knowledgeRecords,
  loadingKnowledge,
  uploading,
  loadKnowledge,
  uploadKnowledge,
} = useWorkspace()

const title = ref('')
const source = ref('internal_upload')
const department = ref('unknown')
const version = ref('v1')
const status = ref('active')
const effectiveFrom = ref('')
const effectiveTo = ref('')
const owner = ref('')
const selectedFile = ref<File | null>(null)
const dragActive = ref(false)
const uploadError = ref('')
const lastUpload = ref<{ message: string; record: KnowledgeRecord } | null>(null)
const selectedRecord = ref<KnowledgeRecord | null>(null)
const detailOpen = ref(false)
const detailTrigger = ref<HTMLButtonElement | null>(null)
const filters = reactive({
  query: '',
  department: '',
  status: '',
  version: '',
})

const filteredRecords = computed(() => filterKnowledgeRecords(knowledgeRecords.value, filters))
const departments = computed(() => uniqueKnowledgeValues(knowledgeRecords.value, 'department'))
const statuses = computed(() => uniqueKnowledgeValues(knowledgeRecords.value, 'status'))
const versions = computed(() => uniqueKnowledgeValues(knowledgeRecords.value, 'version'))
const activeRecordCount = computed(() => (
  knowledgeRecords.value.filter((record) => knowledgeStatusTone(record.status) === 'active').length
))
const hasFilters = computed(() => Object.values(filters).some(Boolean))

const fileSummary = computed(() => {
  if (!selectedFile.value) return '支持 TXT、Markdown、CSV、JSON、PDF、DOCX、PY、LOG，单个文件不超过 20 MB'
  return formatFileSize(selectedFile.value.size)
})

function displayName(record: KnowledgeRecord): string {
  return record.title || record.original_filename || '未命名文档'
}

function clearFilters(): void {
  filters.query = ''
  filters.department = ''
  filters.status = ''
  filters.version = ''
}

function setFile(file?: File): void {
  const candidate = file ?? null
  const error = validateKnowledgeFile(candidate)
  if (error) {
    selectedFile.value = null
    uploadError.value = error
    return
  }
  selectedFile.value = candidate
  uploadError.value = ''
  lastUpload.value = null
}

function chooseFile(event: Event): void {
  const input = event.target as HTMLInputElement
  setFile(input.files?.[0])
  input.value = ''
}

function dropFile(event: DragEvent): void {
  dragActive.value = false
  setFile(event.dataTransfer?.files?.[0])
}

function removeSelectedFile(): void {
  selectedFile.value = null
  uploadError.value = ''
}

async function submitUpload(): Promise<void> {
  const validationError = validateKnowledgeFile(selectedFile.value)
  if (validationError) {
    uploadError.value = validationError
    return
  }
  if (!selectedFile.value || uploading.value) return

  uploadError.value = ''
  lastUpload.value = null
  try {
    const response = await uploadKnowledge(
      selectedFile.value,
      title.value.trim(),
      source.value.trim(),
      {
        department: department.value,
        version: version.value,
        status: status.value,
        effective_from: effectiveFrom.value,
        effective_to: effectiveTo.value,
        owner: owner.value,
        access_scope: 'internal',
      },
    )
    lastUpload.value = response
    title.value = ''
    selectedFile.value = null
  } catch (error) {
    uploadError.value = readableKnowledgeUploadError(error)
  }
}

function openDetails(record: KnowledgeRecord, event: MouseEvent): void {
  selectedRecord.value = record
  detailTrigger.value = event.currentTarget as HTMLButtonElement
  detailOpen.value = true
}

function closeDetails(): void {
  detailOpen.value = false
  selectedRecord.value = null
  void nextTick(() => detailTrigger.value?.focus())
}
</script>

<template>
  <section class="knowledge-page">
    <header class="workspace-header knowledge-header">
      <div>
        <p class="eyebrow">KNOWLEDGE OPERATIONS</p>
        <h1>知识库</h1>
      </div>
      <div class="knowledge-stats" aria-label="知识库统计">
        <span><strong>{{ knowledgeRecords.length }}</strong> 份文档</span>
        <span><strong>{{ activeRecordCount }}</strong> 份生效</span>
      </div>
    </header>

    <div class="knowledge-grid">
      <section class="upload-panel" aria-labelledby="upload-title">
        <span class="chapter-mark">01 / INGEST</span>
        <h2 id="upload-title">添加内部文档</h2>
        <p>提交后由后端完成解析、切块、去重和索引。本页面只展示接口返回的最终结果，不虚构处理阶段进度。</p>

        <div class="form-grid upload-meta-grid">
          <label>
            <span>知识标题</span>
            <input v-model="title" type="text" maxlength="200" placeholder="留空时使用文件名" :disabled="uploading" />
          </label>
          <label>
            <span>来源标签</span>
            <input v-model="source" type="text" maxlength="100" placeholder="例如：internal_upload" :disabled="uploading" />
          </label>
          <label>
            <span>所属部门</span>
            <input v-model="department" type="text" maxlength="100" placeholder="例如：Finance" :disabled="uploading" />
          </label>
          <label>
            <span>版本</span>
            <input v-model="version" type="text" maxlength="50" placeholder="例如：v1" :disabled="uploading" />
          </label>
          <label>
            <span>生效状态</span>
            <select v-model="status" :disabled="uploading">
              <option value="active">已生效</option>
              <option value="draft">草稿</option>
              <option value="deprecated">已停用</option>
              <option value="archived">已归档</option>
            </select>
          </label>
          <label>
            <span>负责人</span>
            <input v-model="owner" type="text" maxlength="100" placeholder="可选" :disabled="uploading" />
          </label>
          <label>
            <span>生效日期</span>
            <input v-model="effectiveFrom" type="date" :disabled="uploading" />
          </label>
          <label>
            <span>失效日期</span>
            <input v-model="effectiveTo" type="date" :disabled="uploading" />
          </label>
        </div>

        <div
          class="drop-zone"
          :class="{ active: dragActive, invalid: uploadError }"
          @dragenter.prevent="dragActive = true"
          @dragover.prevent="dragActive = true"
          @dragleave.prevent="dragActive = false"
          @drop.prevent="dropFile"
        >
          <input
            id="knowledge-file"
            type="file"
            :accept="KNOWLEDGE_FILE_ACCEPT"
            :disabled="uploading"
            @change="chooseFile"
          />
          <label for="knowledge-file" class="drop-zone-content">
            <span class="drop-index"><el-icon><DocumentAdd /></el-icon></span>
            <strong>{{ selectedFile?.name ?? '选择或拖入企业文档' }}</strong>
            <span>{{ fileSummary }}</span>
          </label>
          <button
            v-if="selectedFile"
            class="file-clear"
            type="button"
            aria-label="移除已选择的文件"
            @click.prevent="removeSelectedFile"
          >
            <el-icon><Close /></el-icon>
          </button>
        </div>
        <p v-if="uploadError" class="field-error" role="alert">{{ uploadError }}</p>

        <button
          class="primary-button upload-button"
          type="button"
          :disabled="!selectedFile || uploading"
          @click="submitUpload"
        >
          {{ uploading ? '正在上传…' : '上传文档' }}
        </button>

        <div
          v-if="lastUpload"
          class="upload-result"
          :class="{ duplicate: lastUpload.record.deduplicated }"
          role="status"
        >
          <p class="eyebrow">{{ lastUpload.record.deduplicated ? 'DUPLICATE CHECK' : 'INGEST RESULT' }}</p>
          <h3>{{ lastUpload.record.deduplicated ? '检测到重复文件，未重复入库' : '文件已完成入库' }}</h3>
          <p>{{ lastUpload.message }}</p>
          <span v-if="lastUpload.record.similarity !== null && lastUpload.record.similarity !== undefined">
            相似度：{{ (lastUpload.record.similarity * 100).toFixed(1) }}%
          </span>
        </div>
      </section>

      <section class="library-panel" aria-labelledby="library-title">
        <div class="library-heading">
          <div>
            <span class="chapter-mark">02 / LIBRARY</span>
            <h2 id="library-title">文档目录</h2>
          </div>
          <button class="secondary-button icon-text-button" type="button" :disabled="loadingKnowledge" @click="loadKnowledge">
            <el-icon><Refresh /></el-icon>
            {{ loadingKnowledge ? '刷新中…' : '刷新列表' }}
          </button>
        </div>

        <div class="document-filters" role="search" aria-label="筛选知识库文档">
          <label class="filter-search">
            <span class="sr-only">搜索文档</span>
            <el-icon aria-hidden="true"><Search /></el-icon>
            <input v-model="filters.query" type="search" placeholder="搜索标题、文件名、来源或内容摘要" />
          </label>
          <label>
            <span class="sr-only">部门</span>
            <select v-model="filters.department" aria-label="按部门筛选">
              <option value="">全部部门</option>
              <option v-for="value in departments" :key="value" :value="value">{{ value }}</option>
            </select>
          </label>
          <label>
            <span class="sr-only">状态</span>
            <select v-model="filters.status" aria-label="按状态筛选">
              <option value="">全部状态</option>
              <option v-for="value in statuses" :key="value" :value="value">{{ knowledgeStatusLabel(value) }}</option>
            </select>
          </label>
          <label>
            <span class="sr-only">版本</span>
            <select v-model="filters.version" aria-label="按版本筛选">
              <option value="">全部版本</option>
              <option v-for="value in versions" :key="value" :value="value">{{ value }}</option>
            </select>
          </label>
          <button v-if="hasFilters" class="filter-clear" type="button" @click="clearFilters">
            清除筛选
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
        <div v-else-if="filteredRecords.length === 0" class="library-empty compact">
          <span class="empty-rule" />
          <h3>没有匹配的文档</h3>
          <p>尝试调整搜索词或筛选条件。</p>
          <button class="secondary-button" type="button" @click="clearFilters">清除筛选</button>
        </div>
        <div v-else class="document-list">
          <article v-for="(record, index) in filteredRecords" :key="String(record.source_id || record.id || `${displayName(record)}-${index}`)" class="document-row">
            <span class="document-index">{{ String(index + 1).padStart(2, '0') }}</span>
            <div class="document-main">
              <h3 :title="displayName(record)">{{ displayName(record) }}</h3>
              <p :title="record.original_filename || ''">{{ record.original_filename || '原始文件名未记录' }}</p>
              <div class="document-tags">
                <span v-if="record.department">{{ record.department }}</span>
                <span v-if="record.version">{{ record.version }}</span>
                <span v-if="record.status" class="status-badge" :class="`status-${knowledgeStatusTone(record.status)}`">
                  {{ knowledgeStatusLabel(record.status) }}
                </span>
              </div>
            </div>
            <div class="document-meta">
              <span>{{ record.source || '来源未知' }}</span>
              <span>{{ formatKnowledgeDateRange(record.effective_from, record.effective_to) }}</span>
              <span v-if="record.owner">{{ record.owner }}</span>
            </div>
            <button class="document-details-button" type="button" @click="openDetails(record, $event)">
              查看详情
            </button>
          </article>
        </div>
        <p v-if="!loadingKnowledge && knowledgeRecords.length > 0" class="library-result-count" aria-live="polite">
          显示 {{ filteredRecords.length }} / {{ knowledgeRecords.length }} 份文档
        </p>
      </section>
    </div>

    <DocumentDetailDrawer
      :open="detailOpen"
      :record="selectedRecord"
      @close="closeDetails"
    />
  </section>
</template>
