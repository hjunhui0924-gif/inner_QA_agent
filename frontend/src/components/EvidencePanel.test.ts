import { createApp, defineComponent, h, nextTick, reactive } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ fetchKnowledgeRecord: vi.fn(), fetchSourcePreview: vi.fn(), downloadKnowledge: vi.fn() }))
vi.mock('../services/api', () => api)

import EvidencePanel from './EvidencePanel.vue'
import type { Citation, SourcePreviewResponse } from '../types/api'

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
    sessionId: 'session-a',
    turnId: 'turn-a',
    citations: records,
  })
  const events: string[] = []
  host = document.createElement('div')
  document.body.appendChild(host)

  const Root = defineComponent({
    setup() {
      return () => h(EvidencePanel, {
        open: state.open,
        modal: true,
        citations: state.citations,
        sessionId: state.sessionId,
        turnId: state.turnId,
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
  Object.values(api).forEach((mock) => mock.mockReset())
})

const targetCitation = { citation_id: 'C1', source_id: 'source-a', title: '来源', quote: '经理审批。' }
function previewFor(sourceId: string): SourcePreviewResponse {
  return { source_id: sourceId, title: '来源', version: 'v2', content_checksum: 'checksum', format: 'text', render_mode: 'extracted_text',
    revision_match: 'changed', original_available: false, previous_cursor: null, next_cursor: 'next',
    blocks: [{ id: 'block', text: '经理审批。', page: null, section: '审批' }],
    location: { status: 'exact', block_id: 'block', page: null, section: '审批', start: 0, end: 5, match_count: 1 } }
}
function sourceButton() {
  return [...(host?.querySelectorAll<HTMLButtonElement>('.evidence-card button') || [])]
    .find((button) => button.textContent?.includes('查看文档信息与下载'))
}

describe('EvidencePanel', () => {
  it('keeps C1 tied to its own answer and discards an older request after changing sessions', async () => {
    let resolveOld!: (value: object) => void
    api.fetchKnowledgeRecord.mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve }))
    api.fetchKnowledgeRecord.mockResolvedValue({ source_id: 'source-b', title: '当前来源', original_filename: 'policy.md' })
    api.fetchSourcePreview.mockResolvedValue(previewFor('source-b'))
    const { state } = mountPanel([targetCitation])
    state.open = true
    await nextTick()
    sourceButton()?.click()
    await nextTick()
    const oldSignal = api.fetchKnowledgeRecord.mock.calls[0]?.[1] as AbortSignal
    state.sessionId = 'session-b'
    state.turnId = 'turn-b'
    state.citations = [{ ...targetCitation, source_id: 'source-b' }]
    await nextTick()
    expect(oldSignal.aborted).toBe(true)
    sourceButton()?.click()
    await vi.waitFor(() => expect(host?.querySelector('.document-detail-drawer')?.textContent).toContain('当前来源'))
    resolveOld({ source_id: 'source-a', title: '错误的旧来源', content: 'old private text' })
    await nextTick()
    await nextTick()
    expect(host?.textContent).not.toContain('错误的旧来源')
    expect(api.fetchSourcePreview).toHaveBeenCalledOnce()
    expect(api.fetchSourcePreview.mock.calls[0]?.slice(0, 2)).toEqual(['source-b', { sessionId: 'session-b', turnId: 'turn-b', citationId: 'C1' }])
  })

  it('clears cached source text when access is revoked while paging', async () => {
    api.fetchKnowledgeRecord.mockResolvedValue({ source_id: 'source-a', title: 'private-source', original_filename: 'policy.md' })
    api.fetchSourcePreview.mockResolvedValueOnce(previewFor('source-a')).mockRejectedValueOnce({ status: 403 })
    const { state } = mountPanel([targetCitation])
    state.open = true
    await nextTick()
    sourceButton()?.click()
    await vi.waitFor(() => expect(host?.querySelector('.source-preview-body')).not.toBeNull())
    expect(host?.textContent).toContain('引用时的版本不同')
    expect(host?.querySelector<HTMLButtonElement>('.detail-actions button')?.disabled).toBe(true)
    const next = [...(host?.querySelectorAll<HTMLButtonElement>('button') || [])].find((button) => button.textContent === '下一窗口')
    next?.click()
    await vi.waitFor(() => expect(host?.querySelector('.document-detail-drawer [role="alert"]')?.textContent).toContain('不可访问'))
    expect(host?.querySelector('.source-preview-body')).toBeNull()
    expect(host?.querySelector('.document-detail-drawer')?.textContent).not.toContain('private-source')
  })

  it('aborts a closed preview and ignores its late result', async () => {
    api.fetchKnowledgeRecord.mockResolvedValue({ source_id: 'source-a', title: '来源', original_filename: 'policy.md' })
    let resolveOld!: (value: SourcePreviewResponse) => void
    api.fetchSourcePreview.mockImplementation(() => new Promise((resolve) => { resolveOld = resolve }))
    const { state } = mountPanel([targetCitation])
    state.open = true
    await nextTick()
    sourceButton()?.click()
    await vi.waitFor(() => expect(api.fetchSourcePreview).toHaveBeenCalledOnce())
    const signal = api.fetchSourcePreview.mock.calls[0]?.[2] as AbortSignal
    host?.querySelector<HTMLButtonElement>('[aria-label="关闭文档详情"]')?.click()
    await nextTick()
    expect(signal.aborted).toBe(true)
    resolveOld(previewFor('source-a'))
    await nextTick()
    expect(host?.querySelector('.source-preview-body')).toBeNull()
  })
  it('shows a safe web source without presenting generated text as a quote', () => {
    mountPanel([{ citation_id: 'C3', title: 'Python', url: 'https://www.python.org/', source: 'web', quote: '', verification_status: 'web_source' }])
    const link = host?.querySelector<HTMLAnchorElement>('a')
    expect(link?.href).toBe('https://www.python.org/')
    expect(link?.rel).toBe('noopener noreferrer')
    expect(host?.textContent).toContain('搜索服务提供的来源')
    expect(host?.querySelector('blockquote')).toBeNull()
    expect(host?.querySelector('.evidence-number')?.textContent).toBe('[1]')
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
