import { describe, expect, it } from 'vitest'

import { createSessionState } from './sessionState'

describe('session state', () => {
  it('keeps drafts isolated by session while exposing a stable current view', () => {
    const state = createSessionState(() => 'session-a')
    state.draft.value = '草稿 A'
    state.sessionId.value = 'session-b'
    state.draft.value = '草稿 B'
    state.sessionId.value = 'session-a'

    expect(state.draft.value).toBe('草稿 A')
    expect(state.currentView.value.scrollTop).toBe(0)
  })
})
