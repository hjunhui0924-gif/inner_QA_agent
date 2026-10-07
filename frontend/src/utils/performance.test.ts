import { afterEach, describe, expect, it } from 'vitest'

import {
  beginTurnTiming,
  clearPerformance,
  performanceSnapshot,
  recordFirstToken,
  recordMarkdownRender,
  recordMessageCounts,
  recordScrollLongTask,
  recordStreamUpdate,
} from './performance'

afterEach(() => clearPerformance())

describe('development performance diagnostics', () => {
  it('records bounded counters without retaining message text', () => {
    const timing = beginTurnTiming('turn-1')
    recordStreamUpdate(timing)
    recordFirstToken(timing)
    recordMarkdownRender(4, timing)
    recordScrollLongTask(60)
    recordMessageCounts(1000, 998)

    const snapshot = performanceSnapshot()
    expect(snapshot.turns[0]).toMatchObject({
      turnId: 'turn-1',
      streamUpdateCount: 1,
      markdownRenderCount: 1,
      markdownRenderMs: 4,
      scrollLongTaskCount: 1,
    })
    expect(snapshot.diagnostics).toMatchObject({
      streamUpdateCount: 1,
      markdownRenderCount: 1,
      scrollLongTaskCount: 1,
      messageCount: 1000,
      visibleMessageCount: 998,
    })
    expect(JSON.stringify(snapshot)).not.toContain('turn text')
  })
})
