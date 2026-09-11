import type { KnowledgeRecord } from '../types/api'

export const KNOWLEDGE_FILE_EXTENSIONS = [
  '.txt',
  '.md',
  '.csv',
  '.json',
  '.pdf',
  '.docx',
  '.py',
  '.log',
] as const

export const KNOWLEDGE_FILE_ACCEPT = KNOWLEDGE_FILE_EXTENSIONS.join(',')
export const MAX_KNOWLEDGE_UPLOAD_BYTES = 20 * 1024 * 1024

export interface KnowledgeFilters {
  query: string
  department: string
  status: string
  version: string
}

const statusLabels: Record<string, string> = {
  active: '已生效',
  deprecated: '已停用',
  draft: '草稿',
  archived: '已归档',
}

export function normalizeKnowledgeValue(value: unknown): string {
  return String(value ?? '').trim().toLocaleLowerCase()
}

export function knowledgeStatusLabel(status: string | null | undefined): string {
  const normalized = normalizeKnowledgeValue(status)
  return statusLabels[normalized] || (normalized ? '状态未知' : '未标记')
}

export function knowledgeStatusTone(status: string | null | undefined): string {
  const normalized = normalizeKnowledgeValue(status)
  if (normalized === 'active') return 'active'
  if (normalized === 'deprecated' || normalized === 'archived') return 'muted'
  if (normalized === 'draft') return 'draft'
  return 'unknown'
}

export function uniqueKnowledgeValues(
  records: KnowledgeRecord[],
  field: 'department' | 'status' | 'version',
): string[] {
  return [...new Set(
    records
      .map((record) => String(record[field] ?? '').trim())
      .filter(Boolean),
  )].sort((first, second) => first.localeCompare(second, 'zh-CN'))
}

export function filterKnowledgeRecords(
  records: KnowledgeRecord[],
  filters: KnowledgeFilters,
): KnowledgeRecord[] {
  const query = normalizeKnowledgeValue(filters.query)
  const department = normalizeKnowledgeValue(filters.department)
  const status = normalizeKnowledgeValue(filters.status)
  const version = normalizeKnowledgeValue(filters.version)

  return records.filter((record) => {
    const searchable = [
      record.title,
      record.original_filename,
      record.source,
      record.source_id,
      record.source_type,
      record.department,
      record.version,
      record.status,
      record.owner,
      record.preview,
    ].map(normalizeKnowledgeValue).join(' ')

    return (
      (!query || searchable.includes(query))
      && (!department || normalizeKnowledgeValue(record.department) === department)
      && (!status || normalizeKnowledgeValue(record.status) === status)
      && (!version || normalizeKnowledgeValue(record.version) === version)
    )
  })
}

export function validateKnowledgeFile(file: File | null): string | null {
  if (!file) return '请选择要上传的文档。'
  if (file.size <= 0) return '文件为空，无法上传。'
  if (file.size > MAX_KNOWLEDGE_UPLOAD_BYTES) return '文件不能超过 20 MB。'

  const filename = file.name.toLocaleLowerCase()
  if (!KNOWLEDGE_FILE_EXTENSIONS.some((extension) => filename.endsWith(extension))) {
    return '暂不支持该文件类型，请选择 TXT、Markdown、CSV、JSON、PDF 或 DOCX 文件。'
  }
  return null
}

export function readableKnowledgeUploadError(error: unknown): string {
  const message = error instanceof Error ? error.message.trim() : ''
  const safeMessages = [
    '上传文件为空。',
    '上传文件不能超过',
    '上传文件解析后内容为空，无法入库。',
    'JSON 文件解析失败。',
    'DOCX 文件结构无效。',
    '当前环境未安装',
  ]
  const knownMessage = safeMessages.find((prefix) => message.includes(prefix))
  if (knownMessage && message.startsWith(knownMessage)) return message
  return '文件上传或入库失败，请检查文件内容后重试。'
}

export function formatFileSize(bytes: number | null | undefined): string {
  const size = Number(bytes)
  if (!Number.isFinite(size) || size < 0) return '大小未知'
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / (1024 * 1024)).toFixed(2)} MB`
}

export function formatKnowledgeDateRange(
  effectiveFrom?: string | null,
  effectiveTo?: string | null,
): string {
  const from = String(effectiveFrom ?? '').trim()
  const to = String(effectiveTo ?? '').trim()
  if (from && to) return `${from} 至 ${to}`
  if (from) return `${from} 起`
  if (to) return `截至 ${to}`
  return '未记录'
}
