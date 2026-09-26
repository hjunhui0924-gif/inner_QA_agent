import { shallowRef } from 'vue'
import type { UiMessage, Citation } from '../types/api'

export interface EvidenceSelection {
  sessionId: string
  messageId: string
  citationId: string | null
  citationsSnapshot: Citation[]
  triggerElement: HTMLElement | null
}
export function useEvidenceSelection() {
  const selection = shallowRef<EvidenceSelection | null>(null)
  function select(
    sessionId: string,
    message: UiMessage,
    triggerElement: HTMLElement | null,
    citationId?: string,
  ) {
    const citations = message.citations ?? []
    if (!citations.length) return
    selection.value = {
      sessionId,
      messageId: message.message_id || message.id,
      citationId:
        citations.find(
          (c) => c.citation_id.toUpperCase() === citationId?.toUpperCase(),
        )?.citation_id ?? citations[0]!.citation_id,
      citationsSnapshot: citations.map((c) => ({ ...c })),
      triggerElement,
    }
  }
  function clear() {
    selection.value = null
  }
  return { selection, select, clear }
}
