import type {
  ChatHistoryItem,
  HealthResponse,
  KnowledgeRecord,
  SessionSummary,
  StreamEvent,
  UploadResponse,
  ChatMode,
  KnowledgeUploadMetadata,
  ContextUsage,
  SourcePreviewResponse,
  FeedbackCategory,
} from '../types/api'
import { assertStreamEvent } from '../utils/streamEvent'

const configuredBase = import.meta.env.VITE_API_BASE_URL?.trim()
const API_BASE = (configuredBase || '/api').replace(/\/$/, '')
const KNOWLEDGE_UPLOAD_METADATA_FIELDS = [
  'department',
  'version',
  'status',
  'effective_from',
  'effective_to',
  'owner',
  'access_scope',
  'required_scopes',
  'allowed_roles',
  'allowed_departments',
  'denied_roles',
  'denied_departments',
  'denied_scopes',
  'public_internal',
] as const

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: unknown; message?: unknown }
    if (typeof payload.detail === 'string') return payload.detail
    if (typeof payload.message === 'string') return payload.message
  } catch {
    // Fall through to the HTTP status when the response is not JSON.
  }
  return `请求失败（HTTP ${response.status}）`
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init)
  if (!response.ok) {
    throw new ApiError(await errorMessage(response), response.status)
  }
  return (await response.json()) as T
}

export function fetchHealth(): Promise<HealthResponse> {
  return requestJson<HealthResponse>('/health')
}

export async function fetchSessions(userId: string): Promise<SessionSummary[]> {
  const payload = await requestJson<{ items: SessionSummary[] }>(
    `/chat/sessions/${encodeURIComponent(userId)}`,
  )
  return Array.isArray(payload.items) ? payload.items : []
}

export async function fetchHistory(
  userId: string,
  sessionId: string,
): Promise<ChatHistoryItem[]> {
  const payload = await requestJson<{ items: ChatHistoryItem[] }>(
    `/chat/history/${encodeURIComponent(userId)}/${encodeURIComponent(sessionId)}`,
  )
  return Array.isArray(payload.items) ? payload.items : []
}

export async function fetchContextUsage(
  userId: string,
  sessionId: string,
): Promise<ContextUsage> {
  const payload = await requestJson<{ session_id: string; context_usage: ContextUsage }>(
    `/chat/context/${encodeURIComponent(userId)}/${encodeURIComponent(sessionId)}`,
  )
  return payload.context_usage
}

export function deleteSession(userId: string, sessionId: string): Promise<{ message: string }> {
  return requestJson<{ message: string }>(
    `/chat/session/${encodeURIComponent(userId)}/${encodeURIComponent(sessionId)}`,
    { method: 'DELETE' },
  )
}

export async function fetchKnowledgeRecords(): Promise<KnowledgeRecord[]> {
  const payload = await requestJson<{ items: KnowledgeRecord[] }>('/knowledge/records')
  return Array.isArray(payload.items) ? payload.items : []
}

export async function downloadKnowledge(sourceId: string): Promise<Blob> {
  const response = await fetch(
    `${API_BASE}/knowledge/records/${encodeURIComponent(sourceId)}/download`,
  )
  if (!response.ok) {
    throw new ApiError(await errorMessage(response), response.status)
  }
  return response.blob()
}

export async function fetchPdfSource(sourceId: string, signal: AbortSignal): Promise<{ bytes: Uint8Array; maxPages: number }> {
  const response = await fetch(
    `${API_BASE}/knowledge/records/${encodeURIComponent(sourceId)}/download?preview=true`,
    { signal },
  )
  if (!response.ok) throw new ApiError(await errorMessage(response), response.status)
  const positiveHeader = (name: string, fallback: number) => {
    const value = Number(response.headers.get(name))
    return Number.isSafeInteger(value) && value > 0 ? value : fallback
  }
  const maxBytes = positiveHeader('X-Pdf-Preview-Max-Bytes', 20 * 1024 * 1024)
  const maxPages = positiveHeader('X-Pdf-Preview-Max-Pages', 500)
  const tooLarge = () => new ApiError('PDF 文件超过预览上限，可下载原文件查看。', 413)
  if (Number(response.headers.get('Content-Length')) > maxBytes) {
    await response.body?.cancel()
    throw tooLarge()
  }
  if (!response.body) throw new ApiError('PDF 文件内容不可用。', 502)
  const reader = response.body.getReader()
  const chunks: Uint8Array[] = []
  let length = 0
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      length += value.byteLength
      if (length > maxBytes) {
        await reader.cancel()
        throw tooLarge()
      }
      chunks.push(value)
    }
  } finally {
    reader.releaseLock()
  }
  const bytes = new Uint8Array(length)
  let offset = 0
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength }
  return { bytes, maxPages }
}

export async function fetchKnowledgeRecord(sourceId: string, signal?: AbortSignal): Promise<KnowledgeRecord> {
  const payload = await requestJson<{ record: KnowledgeRecord }>(
    `/knowledge/records/${encodeURIComponent(sourceId)}`,
    signal ? { signal } : undefined,
  )
  return payload.record
}

export function fetchSourcePreview(
  sourceId: string,
  target?: { sessionId?: string; turnId?: string; citationId?: string; cursor?: string },
  signal?: AbortSignal,
): Promise<SourcePreviewResponse> {
  const params = new URLSearchParams()
  if (target?.sessionId) params.set('session_id', target.sessionId)
  if (target?.turnId) params.set('turn_id', target.turnId)
  if (target?.citationId) params.set('citation_id', target.citationId)
  if (target?.cursor) params.set('cursor', target.cursor)
  const suffix = params.toString() ? `?${params.toString()}` : ''
  return requestJson<SourcePreviewResponse>(
    `/knowledge/records/${encodeURIComponent(sourceId)}/preview${suffix}`,
    { signal },
  )
}

export function submitFeedback(payload: {
  feedback_id: string
  session_id: string
  turn_id: string
  message_id: string
  category: FeedbackCategory
  note: string
}): Promise<{ feedback_id: string; status: string }> {
  return requestJson<{ feedback_id: string; status: string }>('/chat/feedback', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function uploadKnowledge(
  file: File,
  title: string,
  source: string,
  metadata: KnowledgeUploadMetadata = {},
): Promise<UploadResponse> {
  const body = new FormData()
  body.append('file', file)
  body.append('title', title)
  body.append('source', source || 'internal_upload')
  KNOWLEDGE_UPLOAD_METADATA_FIELDS.forEach((key) => {
    const value = metadata[key]
    if (typeof value === 'boolean') body.append(key, String(value))
    else if (value?.trim()) body.append(key, value.trim())
  })
  return requestJson<UploadResponse>('/knowledge/upload', { method: 'POST', body })
}

export async function streamChat(
  payload: { message: string; user_id: string; session_id: string; mode?: ChatMode; web_search?: boolean; turn_id?: string; attempt_group_id?: string; retry_of_turn_id?: string },
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_BASE}/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...payload, mode: payload.mode ?? 'knowledge', web_search: payload.web_search ?? false }),
    signal,
  })
  if (!response.ok) {
    throw new ApiError(await errorMessage(response), response.status)
  }
  if (!response.body) {
    throw new Error('浏览器未收到流式响应。')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let receivedResult = false
  let ended = false
  let readerClosed = false
  const cancelReader = () => { void reader.cancel().catch(() => undefined) }
  signal?.addEventListener('abort', cancelReader, { once: true })

  const consumeBlock = (block: string) => {
    const data = block
      .split(/\r?\n/)
      .filter((line) => line.startsWith('data:'))
      .map((line) => line.slice(5).trimStart())
      .join('\n')
    if (!data) return
    let event: unknown
    try { event = JSON.parse(data) } catch {
      if (receivedResult) return
      throw new Error('无法解析后端返回的 SSE 事件。')
    }
    // Only done remains relevant after the first authoritative result. Ignore
    // provisional or duplicate results before they can trigger side effects.
    if (receivedResult && (!event || typeof event !== 'object' || !('type' in event) || event.type !== 'done')) return
    if (!receivedResult) signal?.throwIfAborted()
    assertStreamEvent(event)
    onEvent(event)
    if (event.type === 'result') receivedResult = true
    if (event.type === 'done') ended = true
  }

  try {
    signal?.throwIfAborted()
    while (!ended) {
      let chunk: ReadableStreamReadResult<Uint8Array>
      try { chunk = await reader.read() } catch (error) {
        if (receivedResult) break
        throw error
      }
      if (!receivedResult) signal?.throwIfAborted()
      const { done, value } = chunk
      readerClosed = done
      buffer += decoder.decode(value, { stream: !done })
      const blocks = buffer.split(/\r?\n\r?\n/)
      buffer = blocks.pop() ?? ''
      for (const block of blocks) {
        consumeBlock(block)
        if (ended) break
      }
      if (done) break
    }
    // SSE frames dispatch on a blank line; an unterminated tail is incomplete.
    if (!receivedResult) throw new Error('后端流式响应未返回经过校验的最终结果。')
  } finally {
    signal?.removeEventListener('abort', cancelReader)
    if (!readerClosed) cancelReader()
    reader.releaseLock()
  }
}
