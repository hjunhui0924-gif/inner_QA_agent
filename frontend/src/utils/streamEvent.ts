import type { StreamEvent } from '../types/api'

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}
const string = (value: unknown) => typeof value === 'string'
const optionalString = (value: unknown) => value == null || string(value)
const number = (value: unknown) => typeof value === 'number' && Number.isFinite(value) && value >= 0
const oneOf = (value: unknown, choices: readonly string[]) => typeof value === 'string' && choices.includes(value)
const stepStatuses = ['running', 'complete', 'skipped', 'error', 'cancelled']
const summaryStepStatuses = ['pending', ...stepStatuses]

function citation(value: unknown): boolean {
  return record(value) && string(value.citation_id) && Boolean(value.citation_id) && string(value.title) && string(value.quote)
    && ['source_id', 'evidence_id', 'document_id', 'url', 'filename', 'section', 'chunk_id', 'source_content_checksum', 'verification_status'].every((key) => optionalString(value[key]))
    && (value.page == null || (number(value.page) && Number.isInteger(value.page) && Number(value.page) > 0))
}

function contextUsage(value: unknown): boolean {
  return value == null || (record(value) && value.scope === 'conversation_history' && value.estimation_method === 'mixed_text_v1'
    && value.estimated === true && oneOf(value.status, ['available', 'unavailable'])
    && (value.used_tokens === null || number(value.used_tokens)) && (value.summary_tokens === null || number(value.summary_tokens))
    && number(value.budget_tokens) && number(value.summary_trigger_tokens) && string(value.sampled_at) && optionalString(value.checkpoint_id))
}

function processSummary(value: unknown): boolean {
  return value == null || (record(value) && oneOf(value.status, ['complete', 'fallback', 'error', 'cancelled'])
    && (value.elapsed_ms == null || number(value.elapsed_ms)) && Array.isArray(value.steps)
    && value.steps.every((step) => record(step) && string(step.node) && oneOf(step.status, summaryStepStatuses)
      && (step.duration_ms == null || number(step.duration_ms))))
}

/** Validate the wire contract before any event can change answer UI state. */
export function assertStreamEvent(value: unknown): asserts value is StreamEvent {
  let valid = false
  if (record(value)) {
    switch (value.type) {
      case 'status': valid = string(value.node) && string(value.content) && (value.node_status == null || oneOf(value.node_status, stepStatuses)); break
      case 'token': valid = string(value.content); break
      case 'preview_delta': valid = string(value.content) && string(value.generation_id) && Boolean(value.generation_id); break
      case 'done': valid = string(value.trace_id); break
      case 'result':
        valid = string(value.content) && string(value.trace_id) && string(value.failure_type)
          && Array.isArray(value.citations) && value.citations.every(citation)
          && ['turn_id', 'attempt_group_id', 'retry_of_turn_id', 'failure_stage', 'failure_reason'].every((key) => optionalString(value[key]))
          && (value.mode == null || value.mode === 'knowledge' || value.mode === 'general')
          && (value.web_search == null || typeof value.web_search === 'boolean')
          && contextUsage(value.context_usage) && processSummary(value.process_summary)
        break
    }
  }
  if (!valid) throw new Error('后端返回的 SSE 事件字段无效。')
}
