export interface Citation {
  citation_id: string
  source_id?: string
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

export type ChatMode = 'knowledge' | 'general'

export type AnswerState =
  | 'streaming'
  | 'validating'
  | 'complete'
  | 'fallback'
  | 'error'
  | 'cancelled'

export type AgentStepStatus =
  | 'pending'
  | 'running'
  | 'complete'
  | 'skipped'
  | 'error'

export interface ChatHistoryItem {
  role: 'user' | 'assistant'
  content: string
  citations?: Citation[]
  turn_id?: string | null
  message_id?: string | null
  failure_type?: string | null
  failure_stage?: string | null
  failure_reason?: string | null
  trace_id?: string | null
}

export interface KnowledgeRecord {
  id?: number | string
  source_id?: string
  title: string
  source: string
  source_type?: string
  department?: string
  version?: string
  status?: string
  effective_from?: string | null
  effective_to?: string | null
  owner?: string
  access_scope?: string
  original_filename?: string
  content?: string
  content_length?: number
  preview?: string
  content_fingerprint?: string
  content_checksum?: string
  deduplicated?: boolean
  dedup_type?: 'exact' | 'near' | 'similar' | 'none' | string
  similarity?: number | null
}

export interface KnowledgeUploadMetadata {
  department?: string
  version?: string
  status?: string
  effective_from?: string
  effective_to?: string
  owner?: string
  access_scope?: string
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
  | {
      type: 'status'
      node: string
      content: string
      node_status?: Exclude<AgentStepStatus, 'pending'>
    }
  | { type: 'token'; content: string }
  | {
      type: 'result'
      content: string
      citations: Citation[]
      trace_id: string
      turn_id?: string
      failure_type: string
      failure_stage?: string | null
      failure_reason?: string | null
      budget_snapshot?: Record<string, unknown> | null
  }
  | { type: 'done'; trace_id: string }

export interface UiMessage extends ChatHistoryItem {
  id: string
  state: 'complete' | 'streaming' | 'error' | 'cancelled'
  answerState?: AnswerState
  failureType?: string | null
  failureStage?: string | null
  failureReason?: string | null
  traceId?: string | null
}

export interface AgentStep {
  node: string
  content: string
  status?: AgentStepStatus
  startedAt?: number
  endedAt?: number
}

export interface ToastMessage {
  id: string
  tone: 'success' | 'warning' | 'error' | 'info'
  title: string
  detail?: string
}
