import { describe, it, expect } from 'vitest'
import { useEvidenceSelection } from './useEvidenceSelection'
import type { UiMessage } from '../types/api'
function answer(id: string, title: string): UiMessage {
  return { id, role: 'assistant', content: title, state: 'complete', answerState: 'complete', citations: [{ citation_id: 'C1', title, quote: title }] }
}
describe('evidence context', () => {
  it('binds repeated C1 identifiers to a message snapshot, preserving the real trigger', () => {
    const evidence = useEvidenceSelection()
    const first = answer('first','旧原文')
    const second = answer('second','新原文')
    const trigger = document.createElement('button')
    evidence.select('session', first, trigger,'C1')
    first.citations![0]!.title = 'changed'
    expect(evidence.selection.value?.citationsSnapshot[0]?.title).toBe('旧原文')
    expect(evidence.selection.value?.messageId).toBe('first')
    expect(evidence.selection.value?.triggerElement).toBe(trigger)
    evidence.select('session',second,trigger,'C1')
    expect(evidence.selection.value?.messageId).toBe('second')
    evidence.clear()
    expect(evidence.selection.value).toBeNull()
  })
  it('does not open evidence for a message without citations', () => {
    const evidence = useEvidenceSelection()
    evidence.select('session', { ...answer('empty',''), citations: [] }, null)
    expect(evidence.selection.value).toBeNull()
  })
})
