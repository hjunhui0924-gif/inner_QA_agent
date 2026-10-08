import { reactive, ref } from 'vue'

import type { KnowledgeRecord, KnowledgeUploadMetadata, UploadResponse } from '../types/api'

export function createKnowledgeState() {
  const uploadForm = reactive({
    title: '',
    source: 'internal_upload',
    department: 'unknown',
    version: 'v1',
    status: 'active',
    effectiveFrom: '',
    effectiveTo: '',
    owner: '',
    selectedFile: null as File | null,
    uploadError: '',
    lastUpload: null as UploadResponse | null,
  })
  const uploadTask = reactive({
    file: null as File | null,
    metadata: {} as KnowledgeUploadMetadata,
    result: null as UploadResponse | null,
    error: '',
    title: '',
    source: '',
  })
  return {
    knowledgeRecords: ref<KnowledgeRecord[]>([]),
    loadingKnowledge: ref(false),
    knowledgeError: ref(''),
    uploading: ref(false),
    uploadForm,
    uploadTask,
  }
}
