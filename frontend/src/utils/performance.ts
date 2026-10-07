/** Bounded, local diagnostics. No question, answer or source text is recorded. */
export interface TurnTiming {
  turnId: string
  started: number
  feedbackMs?: number
  firstStatusMs?: number
  firstTokenMs?: number
  resultMs?: number
  visibleMs?: number
  firstVisibleAnswerMs?: number
  streamEndedMs?: number
  readyMs?: number
  streamUpdateCount?: number
  markdownRenderCount?: number
  markdownRenderMs?: number
  scrollLongTaskCount?: number
}
const turns: TurnTiming[] = []
const refreshes: number[] = []
const diagnostics = {
  streamUpdateCount: 0,
  markdownRenderCount: 0,
  markdownRenderMs: 0,
  scrollLongTaskCount: 0,
  messageCount: 0,
  visibleMessageCount: 0,
}
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
export function recordStreamUpdate(timing?: TurnTiming): void {
  diagnostics.streamUpdateCount += 1
  if (timing) timing.streamUpdateCount = (timing.streamUpdateCount || 0) + 1
}
export function recordFirstToken(timing: TurnTiming): void {
  if (timing.firstTokenMs === undefined)
    timing.firstTokenMs = performance.now() - timing.started
}
export function recordMarkdownRender(durationMs: number, timing?: TurnTiming): void {
  diagnostics.markdownRenderCount += 1
  diagnostics.markdownRenderMs += Math.max(0, durationMs)
  const target = timing || turns.at(-1)
  if (target) {
    target.markdownRenderCount = (target.markdownRenderCount || 0) + 1
    target.markdownRenderMs = (target.markdownRenderMs || 0) + Math.max(0, durationMs)
  }
}
export function recordScrollLongTask(durationMs: number): void {
  if (durationMs < 50) return
  diagnostics.scrollLongTaskCount += 1
  const timing = turns.at(-1)
  if (timing) timing.scrollLongTaskCount = (timing.scrollLongTaskCount || 0) + 1
}
export function recordMessageCounts(messageCount: number, visibleMessageCount: number): void {
  diagnostics.messageCount = Math.max(0, messageCount)
  diagnostics.visibleMessageCount = Math.max(0, visibleMessageCount)
}
export function performanceSnapshot() {
  return {
    turns: turns.map((t) => ({ ...t })),
    sessionRefreshMs: [...refreshes],
    diagnostics: { ...diagnostics },
  }
}
export function clearPerformance() {
  turns.length = 0
  refreshes.length = 0
  diagnostics.streamUpdateCount = 0
  diagnostics.markdownRenderCount = 0
  diagnostics.markdownRenderMs = 0
  diagnostics.scrollLongTaskCount = 0
  diagnostics.messageCount = 0
  diagnostics.visibleMessageCount = 0
}
if (import.meta.env.DEV)
  Object.assign(window, {
    workspacePerformance: {
      snapshot: performanceSnapshot,
      clear: clearPerformance,
    },
  })
