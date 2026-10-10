export interface Citation {
  citation_id: string
  evidence_id?: string
  source_id?: string
  document_id?: string
  title: string
  source?: string
  source_content_checksum?: string | null
  url?: string
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
export type ModelProvider = 'qwen' | 'deepseek'

export interface ContextUsage {
  scope: 'conversation_history'
  estimation_method: 'mixed_text_v1'
  estimated: true
  status: 'available' | 'unavailable'
  used_tokens: number | null
  summary_tokens: number | null
  budget_tokens: number
  summary_trigger_tokens: number
  sampled_at: string
  checkpoint_id: string | null
}

export type FeedbackCategory = 'incorrect_answer' | 'citation_mismatch' | 'outdated_policy' | 'not_helpful' | 'other'

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
  | 'cancelled'

export interface TurnProcessStep {
  node: string
  status: AgentStepStatus | 'cancelled'
  duration_ms: number | null
}

export interface TurnProcessSummary {
  status: 'complete' | 'fallback' | 'error' | 'cancelled'
  elapsed_ms: number | null
  steps: TurnProcessStep[]
}

export interface ChatHistoryItem {
  attempt_group_id?: string | null
  retry_of_turn_id?: string | null
  mode?: ChatMode
  web_search?: boolean
  model_provider?: ModelProvider
  role: 'user' | 'assistant'
  content: string
  citations?: Citation[]
  turn_id?: string | null
  message_id?: string | null
  failure_type?: string | null
  failure_stage?: string | null
  failure_reason?: string | null
  trace_id?: string | null
  process_summary?: TurnProcessSummary | null
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
  effective_from_provided?: boolean | null
  effective_from?: string | null
  effective_to?: string | null
  owner?: string
  access_scope?: string
  required_scopes?: string[]
  allowed_roles?: string[]
  allowed_departments?: string[]
  denied_roles?: string[]
  denied_departments?: string[]
  denied_scopes?: string[]
  public_internal?: boolean
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

export interface SourcePreviewBlock {
  id: string
  text: string
  page: number | null
  section: string | null
}

export interface SourcePreviewResponse {
  source_id: string
  title: string
  version: string | null
  content_checksum: string
  format: 'text' | 'pdf' | 'docx' | 'other'
  render_mode: 'extracted_text'
  revision_match: 'same' | 'changed' | 'unknown'
  location: {
    status: 'exact' | 'normalized' | 'ambiguous' | 'not_found' | 'unavailable'
    block_id: string | null
    page: number | null
    section: string | null
    start: number | null
    end: number | null
    match_count: number
  }
  blocks: SourcePreviewBlock[]
  previous_cursor: string | null
  next_cursor: string | null
  original_available: boolean
}

export interface KnowledgeUploadMetadata {
  department?: string
  version?: string
  status?: string
  effective_from?: string
  effective_to?: string
  owner?: string
  access_scope?: string
  required_scopes?: string
  allowed_roles?: string
  allowed_departments?: string
  denied_roles?: string
  denied_departments?: string
  denied_scopes?: string
  public_internal?: boolean
}

export interface UploadResponse {
  message: string
  record: KnowledgeRecord
}

export interface HealthResponse {
  status: 'ok'
  model: string
  model_provider?: ModelProvider
  available_models?: ModelProvider[]
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
  | { type: 'preview_delta'; content: string; generation_id: string }
  | {
      type: 'result'
      attempt_group_id?: string | null
      retry_of_turn_id?: string | null
      mode?: ChatMode
      web_search?: boolean
      model_provider?: ModelProvider
      content: string
      citations: Citation[]
      trace_id: string
      turn_id?: string
      failure_type: string
      failure_stage?: string | null
      failure_reason?: string | null
      budget_snapshot?: Record<string, unknown> | null
      context_usage?: ContextUsage
      process_summary?: TurnProcessSummary | null
  }
  | { type: 'done'; trace_id: string }

export interface UiMessage extends ChatHistoryItem {
  previewContent?: string
  id: string
  state: 'complete' | 'streaming' | 'error' | 'cancelled'
  answerState?: AnswerState
  failureType?: string | null
  failureStage?: string | null
  failureReason?: string | null
  traceId?: string | null
  processSummary?: TurnProcessSummary | null
}

export interface AgentStep {
  node: string
  content: string
  status?: AgentStepStatus
  startedAt?: number
  endedAt?: number
  durationMs?: number | null
}

export interface ToastMessage {
  id: string
  tone: 'success' | 'warning' | 'error' | 'info'
  title: string
  detail?: string
}
