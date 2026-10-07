import { createApp, defineComponent, h, nextTick, reactive } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

const downloadMock = vi.hoisted(() => vi.fn())
vi.mock('../services/api', () => ({ downloadKnowledge: downloadMock }))
import DocumentDetailDrawer from './DocumentDetailDrawer.vue'
import type { KnowledgeRecord, SourcePreviewResponse } from '../types/api'

const record: KnowledgeRecord = {
  source_id: 'finance-v2',
  title: '费用报销制度',
  source: 'synthetic_seed',
  source_type: 'policy',
  department: 'Finance',
  version: 'v2',
  status: 'active',
  effective_from: '2026-01-01',
  owner: '财务部',
  original_filename: 'finance-v2.md',
  preview: '单笔超过5000元还需财务负责人复核。',
  content_length: 1234,
  content_checksum: 'checksum-123',
}

let app: ReturnType<typeof createApp> | null = null
let host: HTMLDivElement | null = null

function mountDrawer(preview?: SourcePreviewResponse) {
  const state = reactive({ open: false })
  const events: string[] = []
  host = document.createElement('div')
  document.body.appendChild(host)
  const Root = defineComponent({
    setup() {
      return () => h(DocumentDetailDrawer, {
        open: state.open,
        record,
        preview,
        onClose: () => {
          events.push('close')
          state.open = false
        },
      })
    },
  })
  app = createApp(Root)
  app.mount(host)
  return { state, events }
}

afterEach(() => {
  downloadMock.mockReset()
  app?.unmount()
  host?.remove()
  app = null
  host = null
})

describe('DocumentDetailDrawer', () => {
  it('uses backend code-point offsets for source text containing emoji', async () => {
    const text = '🙂前言，经理审批，结束。'
    const points = Array.from(text)
    const start = points.indexOf('经')
    const preview: SourcePreviewResponse = {
      source_id: 'finance-v2', title: '制度', version: 'v2', content_checksum: 'checksum',
      format: 'text', render_mode: 'extracted_text', revision_match: 'same', original_available: false,
      blocks: [{ id: 'block', text, page: 1, section: null }], previous_cursor: null, next_cursor: null,
      location: { status: 'exact', block_id: 'block', page: 1, section: null, start, end: start + 4, match_count: 1 },
    }
    const { state } = mountDrawer(preview)
    state.open = true
    await nextTick()
    expect(host?.querySelector('.source-preview-block mark')?.textContent).toBe('经理审批')
    expect(host?.querySelector('.source-preview-block p')?.textContent).toBe(text)
  })
  it('renders the real record metadata and opens with focus in the drawer', async () => {
    const { state } = mountDrawer()
    state.open = true
    await nextTick()
    await nextTick()

    expect(host?.textContent).toContain('费用报销制度')
    expect(host?.textContent).toContain('已生效')
    const departmentField = [...(host?.querySelectorAll('.detail-fields > div') ?? [])].find(
      (field) => field.querySelector('dt')?.textContent === '部门',
    )
    expect(departmentField?.querySelector('dd')?.textContent).toBe('财务')
    expect(host?.textContent).toContain('1,234 字')
    expect(host?.querySelector('.document-detail-drawer.open')).not.toBeNull()
    expect(document.activeElement).toBe(host?.querySelector('.detail-drawer-header button'))
  })

  it('closes on Escape and does not offer unsupported download/delete actions', async () => {
    const { state, events } = mountDrawer()
    state.open = true
    await nextTick()

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(events).toEqual(['close'])
    const buttonText = [...(host?.querySelectorAll('button') ?? [])]
      .map((button) => button.textContent || '')
      .join(' ')
    expect(buttonText).toContain('下载原文件')
    expect(buttonText).not.toContain('删除文档')
  })

  it('keeps keyboard focus inside the open drawer', async () => {
    const { state } = mountDrawer()
    state.open = true
    await nextTick()
    await nextTick()

    const close = host?.querySelector<HTMLButtonElement>('.detail-drawer-header button')
    close?.focus()
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true }))

    expect(document.activeElement).toBe(close)
  })
  it('keeps document details after download failure and ignores a closed request', async () => {
    const { state } = mountDrawer()
    state.open = true
    await nextTick()
    downloadMock.mockRejectedValueOnce(new Error('network'))
    host?.querySelector<HTMLButtonElement>('.detail-actions button')?.click()
    await vi.waitFor(() => expect(host?.querySelector('.detail-actions .field-error')?.textContent).toContain('来源下载失败'))
    expect(host?.textContent).toContain('费用报销制度')
    let reject!: (error: Error) => void
    downloadMock.mockImplementationOnce(() => new Promise((_resolve, fail) => { reject = fail }))
    host?.querySelector<HTMLButtonElement>('.detail-actions button')?.click()
    state.open = false
    await nextTick()
    reject(new Error('old request'))
    await nextTick()
    state.open = true
    await nextTick()
    expect(host?.querySelector('.detail-actions .field-error')).toBeNull()
  })

})
