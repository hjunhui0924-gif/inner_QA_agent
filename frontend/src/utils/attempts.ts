import type { UiMessage } from '../types/api'

/** Group only explicitly linked attempts; repeated questions alone are not retries. */
export function groupAttempts(messages: UiMessage[]) {
  const groups = new Map<string, UiMessage[][]>()
  for (let index = 0; index < messages.length; index++) {
    const user = messages[index]!
    if (user.role !== 'user') continue
    const answer = messages[index + 1]
    const pair = answer?.role === 'assistant' ? [user, answer] : [user]
    const identity = user.attempt_group_id || user.turn_id || user.id
    const key = JSON.stringify([identity, user.content])
    const attempts = groups.get(key) || []
    attempts.push(pair)
    groups.set(key, attempts)
  }
  return [...groups].map(([id, attempts]) => ({ id, attempts }))
}
