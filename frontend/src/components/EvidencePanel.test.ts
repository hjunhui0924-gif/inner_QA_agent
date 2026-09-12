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

function mountPanel(records: Citation[] = citations) {
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
        modal: true,
        citations: records,
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
  it('shows a safe web source without presenting generated text as a quote', () => {
    mountPanel([{ citation_id: 'C3', title: 'Python', url: 'https://www.python.org/', source: 'web', quote: '', verification_status: 'web_source' }])
    const link = host?.querySelector<HTMLAnchorElement>('a')
    expect(link?.href).toBe('https://www.python.org/')
    expect(link?.rel).toBe('noopener noreferrer')
    expect(host?.textContent).toContain('搜索服务提供的来源')
    expect(host?.querySelector('blockquote')).toBeNull()
    expect(host?.querySelector('.copy-quote')).toBeNull()
  })

  it.each(['javascript:alert(1)', 'data:text/html,unsafe', 'https://user:password@example.com/'])('does not render unsafe source %s', (url) => {
    mountPanel([{ citation_id: 'C1', title: '不可信来源', url, quote: '' }])
    expect(host?.querySelector('a')).toBeNull()
  })
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

  it('closes on Escape and keeps Tab focus inside the evidence dialog', async () => {
    const { state, events } = mountPanel()
    state.open = true
    await nextTick()
    await nextTick()

    const close = host?.querySelector<HTMLButtonElement>('.evidence-close')
    const copies = host?.querySelectorAll<HTMLButtonElement>('.copy-quote')
    const lastCopy = copies?.[copies.length - 1]
    if (!close || !lastCopy) throw new Error('evidence controls not found')
    lastCopy.focus()
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true }))
    expect(document.activeElement).toBe(close)

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(events).toEqual(['close'])
  })

  it('uses complementary semantics when rendered as a desktop side panel', async () => {
    const state = reactive({ open: true })
    host = document.createElement('div')
    document.body.appendChild(host)
    const Root = defineComponent({
      setup() {
        return () => h(EvidencePanel, {
          open: state.open,
          modal: false,
          citations,
          onClose: () => { state.open = false },
        })
      },
    })
    app = createApp(Root)
    app.mount(host)
    await nextTick()

    const panel = host.querySelector('.evidence-panel')
    expect(panel?.getAttribute('role')).toBe('complementary')
    expect(panel?.hasAttribute('aria-modal')).toBe(false)
  })
})
