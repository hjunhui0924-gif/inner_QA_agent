import type { TurnProcessSummary, UiMessage } from '../types/api'
import type { StreamUiState } from '../utils/streamState'

export function uiStateForAnswer(answerState: UiMessage['answerState']): UiMessage['state'] {
  if (answerState === 'error') return 'error'
  if (answerState === 'cancelled') return 'cancelled'
  if (answerState === 'complete' || answerState === 'fallback') return 'complete'
  return 'streaming'
}

export function processSummaryFromStream(
  streamState: StreamUiState,
  forcedStatus?: 'error' | 'cancelled',
): TurnProcessSummary {
  const steps = streamState.steps.map((step) => ({
    node: step.node,
    status: step.status || 'pending',
    duration_ms: step.startedAt && step.endedAt
      ? Math.max(0, step.endedAt - step.startedAt)
      : null,
  }))
  const status: TurnProcessSummary['status'] = forcedStatus || (
    streamState.answerState === 'fallback' ? 'fallback'
      : streamState.answerState === 'error' ? 'error'
        : streamState.answerState === 'cancelled' ? 'cancelled' : 'complete'
  )
  return { status, elapsed_ms: null, steps }
}
