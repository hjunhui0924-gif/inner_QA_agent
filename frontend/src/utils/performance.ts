/** Bounded, local diagnostics. No question, answer or source text is recorded. */
export interface TurnTiming {
  turnId: string
  started: number
  feedbackMs?: number
  firstStatusMs?: number
  resultMs?: number
  visibleMs?: number
  streamEndedMs?: number
  readyMs?: number
}
const turns: TurnTiming[] = []
const refreshes: number[] = []
export function beginTurnTiming(turnId: string): TurnTiming {
  const timing = { turnId, started: performance.now() }
  turns.push(timing)
  if (turns.length > 100) turns.shift()
  return timing
}
export function afterPaint(callback: () => void) {
  if (typeof requestAnimationFrame !== 'undefined')
    requestAnimationFrame(() => requestAnimationFrame(callback))
}
export function recordRefresh(ms: number) {
  refreshes.push(ms)
  if (refreshes.length > 100) refreshes.shift()
}
export function performanceSnapshot() {
  return {
    turns: turns.map((t) => ({ ...t })),
    sessionRefreshMs: [...refreshes],
  }
}
export function clearPerformance() {
  turns.length = 0
  refreshes.length = 0
}
if (import.meta.env.DEV)
  Object.assign(window, {
    workspacePerformance: {
      snapshot: performanceSnapshot,
      clear: clearPerformance,
    },
  })
