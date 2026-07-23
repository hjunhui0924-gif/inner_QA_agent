import type {
  ChatHistoryItem,
  HealthResponse,
  KnowledgeRecord,
  SessionSummary,
  StreamEvent,
  UploadResponse,
} from '../types/api'

const configuredBase = import.meta.env.VITE_API_BASE_URL?.trim()
const API_BASE = (configuredBase || '/api').replace(/\/$/, '')

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

export function uploadKnowledge(
  file: File,
  title: string,
  source: string,
): Promise<UploadResponse> {
  const body = new FormData()
  body.append('file', file)
  body.append('title', title)
  body.append('source', source || 'internal_upload')
  return requestJson<UploadResponse>('/knowledge/upload', { method: 'POST', body })
}

export async function streamChat(
  payload: { message: string; user_id: string; session_id: string },
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_BASE}/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
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

  const consumeBlock = (block: string) => {
    const data = block
      .split(/\r?\n/)
      .filter((line) => line.startsWith('data:'))
      .map((line) => line.slice(5).trimStart())
      .join('\n')
    if (!data) return
    try {
      const event = JSON.parse(data) as StreamEvent
      if (event.type === 'result') receivedResult = true
      onEvent(event)
    } catch {
      throw new Error('无法解析后端返回的 SSE 事件。')
    }
  }

  while (true) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value, { stream: !done })
    const blocks = buffer.split(/\r?\n\r?\n/)
    buffer = blocks.pop() ?? ''
    blocks.forEach(consumeBlock)
    if (done) break
  }
  if (buffer.trim()) consumeBlock(buffer)
  if (!receivedResult) {
    throw new Error('后端流式响应未返回经过校验的最终结果。')
  }
}
