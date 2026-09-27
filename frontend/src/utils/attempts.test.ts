import { describe, expect, it } from 'vitest'
import { groupAttempts } from './attempts'
import type { UiMessage } from '../types/api'
const pair = (id: string, group?: string): UiMessage[] => ['user', 'assistant'].map((role) => ({ id: id + role, role: role as 'user' | 'assistant', content: role === 'user' ? 'same question' : 'answer', state: 'complete', turn_id: id, attempt_group_id: group }))
describe('retry attempt grouping', () => {
  it('groups only explicitly related attempts, including after a history reload', () => {
    const messages = [...pair('first', 'first'), ...pair('retry', 'first')]
    expect(groupAttempts(JSON.parse(JSON.stringify(messages)))[0]?.attempts).toHaveLength(2)
    expect(groupAttempts([...pair('one'), ...pair('two')])).toHaveLength(2)
  })
  it('never folds unrelated question text even with a conflicting group', () => {
    const second = pair('second', 'first'); second[0]!.content = 'another question'
    expect(groupAttempts([...pair('first', 'first'), ...second])).toHaveLength(2)
  })
})
