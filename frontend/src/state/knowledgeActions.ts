import { ref, type Ref } from 'vue'

import { fetchKnowledgeRecords, uploadKnowledge as uploadKnowledgeRequest } from '../services/api'
import type { KnowledgeUploadMetadata, UploadResponse } from '../types/api'
import { readableKnowledgeUploadError } from '../utils/knowledge'
import type { createKnowledgeState } from './knowledgeState'

interface KnowledgeActionOptions {
  state: ReturnType<typeof createKnowledgeState>
  readableError: (error: unknown) => string
  notify: (tone: 'success' | 'warning' | 'error' | 'info', title: string, detail?: string) => void
}

export function createKnowledgeActions(options: KnowledgeActionOptions) {
  const initialized = ref(false)
  let generation = 0

  async function loadKnowledge(): Promise<void> {
    const requestGeneration = ++generation
    options.state.loadingKnowledge.value = true
    options.state.knowledgeError.value = ''
    try {
      const records = await fetchKnowledgeRecords()
      if (requestGeneration === generation) {
        options.state.knowledgeRecords.value = records
        initialized.value = true
      }
    } catch (error) {
      if (requestGeneration === generation)
        options.state.knowledgeError.value = `知识列表加载失败。${options.readableError(error)}`
    } finally {
      if (requestGeneration === generation) options.state.loadingKnowledge.value = false
    }
  }

  async function uploadKnowledge(
    file: File,
    title: string,
    source: string,
    metadata: KnowledgeUploadMetadata = {},
  ): Promise<UploadResponse> {
    if (options.state.uploading.value) throw new Error('已有文档正在上传并处理。')
    options.state.uploading.value = true
    Object.assign(options.state.uploadTask, {
      file,
      title,
      source,
      metadata: { ...metadata },
      result: null,
      error: '',
    })
    options.state.uploadForm.uploadError = ''
    try {
      const response = await uploadKnowledgeRequest(file, title, source, metadata)
      options.state.uploadTask.result = response
      options.state.uploadForm.lastUpload = response
      options.state.uploadForm.selectedFile = null
      options.state.uploadForm.title = ''
      await loadKnowledge()
      if (response.record.deduplicated)
        options.notify('warning', '检测到重复文件', response.message)
      else options.notify('success', '文件已完成入库', response.message)
      return response
    } catch (error) {
      options.state.uploadTask.error = readableKnowledgeUploadError(error)
      options.state.uploadForm.uploadError = options.state.uploadTask.error
      options.notify('error', '文件上传失败', options.state.uploadTask.error)
      throw error
    } finally {
      options.state.uploadTask.file = null
      options.state.uploading.value = false
    }
  }

  return { initialized, loadKnowledge, uploadKnowledge }
}
