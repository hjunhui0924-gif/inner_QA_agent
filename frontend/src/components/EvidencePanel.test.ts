import { createApp, defineComponent, h, nextTick, reactive } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import EvidencePanel from './EvidencePanel.vue'
import type { Citation } from '../types/api'

const citations: Citation[] = [
  {
    citation_id: 'C1',
    title: '报销管理办法',
    filename: 'expense.md',
    page: 2,
    section: '审批流程',
    chunk_id: 'expense-2',
    quote: '费用报销应按流程提交审批。',
  },
  {
    citation_id: 'C2',
    title: '合同管理制度',
    filename: 'contract.md',
    page: 4,
    section: '归档要求',
    chunk_id: 'contract-4',
    quote: '合同归档应保留完整签署文件。',
  },
]

let app: ReturnType<typeof createApp> | null = null
let host: HTMLDivElement | null = null

function mountPanel() {
  const state = reactive({
    open: false,
    selectedCitationId: null as string | null,
  })
  const events: string[] = []
  host = document.createElement('div')
  document.body.appendChild(host)

  const Root = defineComponent({
    setup() {
      return () => h(EvidencePanel, {
        open: state.open,
        citations,
        selectedCitationId: state.selectedCitationId,
        onClose: () => events.push('close'),
      })
    },
  })
  app = createApp(Root)
  app.mount(host)

  return { state, events }
}

afterEach(() => {
  app?.unmount()
  host?.remove()
  app = null
  host = null
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('EvidencePanel', () => {
  it('focuses and highlights the citation selected from the answer', async () => {
    vi.useFakeTimers()
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
    })
    vi.spyOn(window, 'matchMedia').mockReturnValue({
      matches: true,
      media: '(prefers-reduced-motion: reduce)',
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })
    const { state } = mountPanel()

    state.open = true
    state.selectedCitationId = 'C2'
    await nextTick()
    await nextTick()

    const card = host?.querySelector<HTMLElement>('[data-citation-id="C2"]')
    expect(card).not.toBeNull()
    expect(card?.classList.contains('selected')).toBe(true)
    expect(document.activeElement).toBe(card)
  })

  it('copies the original quote and emits close from the panel button', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })
    const { state, events } = mountPanel()
    state.open = true
    await nextTick()

    const copyButton = host?.querySelector<HTMLButtonElement>('.copy-quote')
    copyButton?.click()
    await vi.waitFor(() => expect(copyButton?.textContent).toContain('已复制'))

    expect(writeText).toHaveBeenCalledWith(citations[0].quote)

    host?.querySelector<HTMLButtonElement>('.evidence-close')?.click()
    expect(events).toEqual(['close'])
  })
})
