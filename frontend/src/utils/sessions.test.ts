import { describe, expect, it } from 'vitest'

import type { SessionSummary } from '../types/api'
import { filterSessions, nextSessionAfterDelete } from './sessions'

const sessions: SessionSummary[] = [
  {
    session_id: 'session-1',
    title: '费用报销',
    created_at: '2026-01-01',
    updated_at: '2026-01-01',
    last_message: '超过5000元需要谁复核？',
  },
  {
    session_id: 'session-2',
    title: '合同归档',
    created_at: '2026-01-02',
    updated_at: '2026-01-02',
    last_message: '合同需要保留哪些材料？',
  },
  {
    session_id: 'session-3',
    title: '请假流程',
    created_at: '2026-01-03',
    updated_at: '2026-01-03',
    last_message: '连续请假需要抄送谁？',
  },
]

describe('session helpers', () => {
  it('searches title, last message, and session ID case-insensitively', () => {
    expect(filterSessions(sessions, '合同').map((session) => session.session_id))
      .toEqual(['session-2'])
    expect(filterSessions(sessions, '5000')).toEqual([sessions[0]])
    expect(filterSessions(sessions, 'SESSION-3')).toEqual([sessions[2]])
    expect(filterSessions(sessions, '   ')).toBe(sessions)
  })

  it('selects the item that takes the deleted item position', () => {
    expect(nextSessionAfterDelete(sessions, 'session-2')).toBe('session-3')
    expect(nextSessionAfterDelete(sessions, 'session-3')).toBe('session-2')
    expect(nextSessionAfterDelete([sessions[0]], 'session-1')).toBeNull()
    expect(nextSessionAfterDelete(sessions, 'missing')).toBeNull()
  })
})
