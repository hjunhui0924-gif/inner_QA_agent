import { createApp, defineComponent, h, nextTick, reactive } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import KnowledgeView from './KnowledgeView.vue'
import { useWorkspace } from '../composables/useWorkspace'
import type { KnowledgeRecord } from '../types/api'

const serviceMocks = vi.hoisted(() => ({
  deleteSession: vi.fn(),
  fetchHealth: vi.fn(),
  fetchHistory: vi.fn(),
  fetchKnowledgeRecords: vi.fn(),
  fetchSessions: vi.fn(),
  streamChat: vi.fn(),
  uploadKnowledge: vi.fn(),
}))

vi.mock('../services/api', () => serviceMocks)

const records: KnowledgeRecord[] = [
  {
    source_id: 'finance-v2',
    title: '费用报销制度',
    source: 'synthetic_seed',
    original_filename: 'finance-v2.md',
    department: 'Finance',
    version: 'v2',
    status: 'active',
    effective_from: '2026-01-01',
    owner: '财务部',
    preview: '单笔超过5000元还需财务负责人复核。',
  },
  {
    source_id: 'hr-v1',
    title: '请假与调休流程（旧版）',
    source: 'synthetic_seed',
    original_filename: 'hr-v1.md',
    department: 'HR',
    version: 'v1',
    status: 'deprecated',
    effective_from: '2025-01-01',
    effective_to: '2025-12-31',
    owner: '人力资源部',
    preview: '连续请假超过五天需同步抄送部门负责人。',
  },
]

let app: ReturnType<typeof createApp> | null = null
let host: HTMLDivElement | null = null

function mountView() {
  const workspace = useWorkspace()
  workspace.knowledgeRecords.value = records
  workspace.loadingKnowledge.value = false
  workspace.uploading.value = false
  host = document.createElement('div')
  document.body.appendChild(host)
  const Root = defineComponent({
    setup() {
      return () => h(KnowledgeView)
    },
  })
  app = createApp(Root)
  app.component('el-icon', defineComponent({
    setup(_, { slots }) {
      return () => h('span', slots.default?.())
    },
  }))
  app.mount(host)
  return workspace
}

afterEach(() => {
  app?.unmount()
  host?.remove()
  const workspace = useWorkspace()
  workspace.knowledgeRecords.value = []
  workspace.loadingKnowledge.value = false
  workspace.uploading.value = false
  serviceMocks.uploadKnowledge.mockReset()
  serviceMocks.fetchKnowledgeRecords.mockReset()
  app = null
  host = null
})

describe('KnowledgeView', () => {
  it('filters the document list and opens a read-only detail drawer', async () => {
    mountView()
    await nextTick()

    expect(host?.querySelectorAll('.document-row')).toHaveLength(2)
    const search = host?.querySelector<HTMLInputElement>('input[type="search"]')
    if (!search) throw new Error('document search input not found')
    search.value = '5000'
    search.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()

    expect(host?.querySelectorAll('.document-row')).toHaveLength(1)
    expect(host?.textContent).toContain('费用报销制度')
    expect(host?.textContent).not.toContain('请假与调休流程（旧版）')

    host?.querySelector<HTMLButtonElement>('.document-details-button')?.click()
    await nextTick()
    expect(host?.querySelector('.document-detail-drawer.open')).not.toBeNull()
    expect(host?.textContent).toContain('内容预览')

    host?.querySelector<HTMLButtonElement>('.detail-drawer-header button')?.click()
    await nextTick()
    expect(document.activeElement).toBe(host?.querySelector('.document-details-button'))
  })

  it('rejects an unsupported file before making an upload request', async () => {
    mountView()
    await nextTick()
    const fileInput = host?.querySelector<HTMLInputElement>('input[type="file"]')
    if (!fileInput) throw new Error('file input not found')
    Object.defineProperty(fileInput, 'files', {
      configurable: true,
      value: [new File(['unsafe'], 'policy.exe')],
    })
    fileInput.dispatchEvent(new Event('change', { bubbles: true }))
    await nextTick()

    expect(host?.querySelector('.field-error')?.textContent).toContain('不支持')
    expect(host?.querySelector<HTMLButtonElement>('.upload-button')?.disabled).toBe(true)
  })

  it('shows the backend upload result and clears the selected file after success', async () => {
    serviceMocks.uploadKnowledge.mockResolvedValue({
      message: '文件已成功入库。',
      record: {
        title: '新制度',
        source: 'internal_upload',
        original_filename: 'new.md',
        content_length: 20,
        deduplicated: false,
      },
    })
    serviceMocks.fetchKnowledgeRecords.mockResolvedValue(records)
    const workspace = mountView()
    await nextTick()

    const fileInput = host?.querySelector<HTMLInputElement>('input[type="file"]')
    if (!fileInput) throw new Error('file input not found')
    Object.defineProperty(fileInput, 'files', {
      configurable: true,
      value: [new File(['new policy'], 'new.md')],
    })
    fileInput.dispatchEvent(new Event('change', { bubbles: true }))
    await nextTick()
    host?.querySelector<HTMLButtonElement>('.upload-button')?.click()
    await vi.waitFor(() => expect(host?.textContent).toContain('文件已完成入库'))

    expect(serviceMocks.uploadKnowledge).toHaveBeenCalledOnce()
    expect(host?.textContent).toContain('文件已完成入库')
    expect(host?.querySelector('.drop-zone strong')?.textContent).toContain('选择或拖入企业文档')
  })
})
