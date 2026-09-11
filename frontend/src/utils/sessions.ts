import type { SessionSummary } from '../types/api'

function normalized(value: unknown): string {
  return String(value ?? '').trim().toLocaleLowerCase()
}

export function filterSessions(
  sessions: SessionSummary[],
  query: string,
): SessionSummary[] {
  const normalizedQuery = normalized(query)
  if (!normalizedQuery) return sessions

  return sessions.filter((session) => (
    [session.title, session.last_message, session.session_id]
      .map(normalized)
      .some((value) => value.includes(normalizedQuery))
  ))
}

export function nextSessionAfterDelete(
  sessions: SessionSummary[],
  deletedSessionId: string,
): string | null {
  const deletedIndex = sessions.findIndex(
    (session) => session.session_id === deletedSessionId,
  )
  if (deletedIndex < 0) return null

  const remaining = sessions.filter(
    (session) => session.session_id !== deletedSessionId,
  )
  return remaining[deletedIndex]?.session_id
    || remaining[deletedIndex - 1]?.session_id
    || null
}
