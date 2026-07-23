export interface Citation {
  citation_id: string
  document_id?: string
  title: string
  source?: string
  filename?: string
  page?: number | null
  section?: string
  chunk_id?: string
  quote: string
  verification_status?: string
}

export interface SessionSummary {
  session_id: string
  title: string
  created_at: string
  updated_at: string
  last_message: string
}

export interface ChatHistoryItem {
  role: 'user' | 'assistant'
  content: string
  citations?: Citation[]
}

export interface KnowledgeRecord {
  title: string
  source: string
  original_filename?: string
  content?: string
  content_length?: number
  deduplicated?: boolean
  dedup_type?: 'exact' | 'near' | 'similar' | 'none' | string
  similarity?: number | null
}

export interface UploadResponse {
  message: string
  record: KnowledgeRecord
}

export interface HealthResponse {
  status: 'ok'
  model: string
  retrieval_strategy: string
}

export type StreamEvent =
  | { type: 'status'; node: string; content: string }
  | { type: 'token'; content: string }
  | {
      type: 'result'
      content: string
      citations: Citation[]
      trace_id: string
      failure_type: string
    }
  | { type: 'done'; trace_id: string }

export interface UiMessage extends ChatHistoryItem {
  id: string
  state: 'complete' | 'streaming' | 'error'
  failureType?: string
}

export interface AgentStep {
  node: string
  content: string
}

export interface ToastMessage {
  id: string
  tone: 'success' | 'warning' | 'error' | 'info'
  title: string
  detail?: string
}
