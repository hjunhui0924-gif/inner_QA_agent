import { createApp, defineComponent, h, nextTick, reactive } from 'vue'
import { afterEach, describe, expect, it } from 'vitest'

import DocumentDetailDrawer from './DocumentDetailDrawer.vue'
import type { KnowledgeRecord } from '../types/api'

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

function mountDrawer() {
  const state = reactive({ open: false })
  const events: string[] = []
  host = document.createElement('div')
  document.body.appendChild(host)
  const Root = defineComponent({
    setup() {
      return () => h(DocumentDetailDrawer, {
        open: state.open,
        record,
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
  app?.unmount()
  host?.remove()
  app = null
  host = null
})

describe('DocumentDetailDrawer', () => {
  it('renders the real record metadata and opens with focus in the drawer', async () => {
    const { state } = mountDrawer()
    state.open = true
    await nextTick()
    await nextTick()

    expect(host?.textContent).toContain('费用报销制度')
    expect(host?.textContent).toContain('已生效')
    expect(host?.textContent).toContain('Finance')
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
    expect(buttonText).not.toContain('下载')
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
})
