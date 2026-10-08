import { describe, expect, it } from 'vitest'

import { processSummaryFromStream, uiStateForAnswer } from './chatPresentation'
import { createStreamState } from '../utils/streamState'

describe('chat presentation state', () => {
  it('maps answer states to stable message states', () => {
    expect(uiStateForAnswer('streaming')).toBe('streaming')
    expect(uiStateForAnswer('validating')).toBe('streaming')
    expect(uiStateForAnswer('complete')).toBe('complete')
    expect(uiStateForAnswer('fallback')).toBe('complete')
    expect(uiStateForAnswer('error')).toBe('error')
    expect(uiStateForAnswer('cancelled')).toBe('cancelled')
  })

  it('preserves explicit terminal overrides for cancelled/error turns', () => {
    const state = createStreamState()
    state.steps = [{ node: 'retrieve', content: '', status: 'running', startedAt: 10, endedAt: 30 }]
    expect(processSummaryFromStream(state, 'cancelled')).toEqual({
      status: 'cancelled',
      elapsed_ms: null,
      steps: [{ node: 'retrieve', status: 'running', duration_ms: 20 }],
    })
  })
})
