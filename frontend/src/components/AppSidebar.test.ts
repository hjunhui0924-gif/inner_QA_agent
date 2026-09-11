import { createApp, defineComponent, h, nextTick, reactive } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import AppSidebar from './AppSidebar.vue'
import { useWorkspace } from '../composables/useWorkspace'
import type { SessionSummary } from '../types/api'

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

const sessions: SessionSummary[] = [
  { session_id: 'session-1', title: '费用报销', created_at: '', updated_at: '', last_message: '超过5000元需要谁复核？' },
  { session_id: 'session-2', title: '合同归档', created_at: '', updated_at: '', last_message: '合同材料清单' },
]

let app: ReturnType<typeof createApp> | null = null
let host: HTMLDivElement | null = null

function mountSidebar(initialOpen = true) {
  const state = reactive({ open: initialOpen })
  const events: string[] = []
  const workspace = useWorkspace()
  workspace.sessions.value = [...sessions]
  workspace.sessionId.value = 'session-1'
  workspace.loadingSessions.value = false
  workspace.sessionsError.value = null
  workspace.loadingHistory.value = false
  workspace.historyError.value = null
  workspace.historyErrorSessionId.value = null
  workspace.sending.value = false
  workspace.sessionTitlePending.value = false
  host = document.createElement('div')
  document.body.appendChild(host)
  const Root = defineComponent({
    setup() {
      return () => h(AppSidebar, {
        open: state.open,
        onClose: () => {
          events.push('close')
          state.open = false
        },
      })
    },
  })
  app = createApp(Root)
  app.component('RouterLink', defineComponent({
    props: { to: { type: String, required: true } },
    setup(_, { slots }) {
      return () => h('a', { href: '#' }, slots.default?.())
    },
  }))
  app.component('el-icon', defineComponent({
    setup(_, { slots }) {
      return () => h('span', slots.default?.())
    },
  }))
  app.mount(host)
  return { events, state, workspace }
}

afterEach(() => {
  app?.unmount()
  host?.remove()
  app = null
  host = null
  serviceMocks.deleteSession.mockReset()
  serviceMocks.fetchHistory.mockReset()
  serviceMocks.fetchSessions.mockReset()
  const workspace = useWorkspace()
  workspace.sessions.value = []
  workspace.sessionsError.value = null
  workspace.historyError.value = null
  workspace.historyErrorSessionId.value = null
  workspace.loadingHistory.value = false
  workspace.loadingSessions.value = false
  workspace.sending.value = false
  workspace.newSession()
})

describe('AppSidebar', () => {
  it('filters sessions by title and last message', async () => {
    mountSidebar()
    const input = host?.querySelector<HTMLInputElement>('.session-search input')
    if (!input) throw new Error('session search input not found')

    input.value = '材料'
    input.dispatchEvent(new Event('input', { bubbles: true }))
    await nextTick()

    expect(host?.querySelectorAll('.session-row')).toHaveLength(1)
    expect(host?.textContent).toContain('合同归档')
    expect(host?.textContent).not.toContain('费用报销')
  })

  it('closes the mobile sidebar on Escape and returns focus after deleting dialog cancellation', async () => {
    const { events } = mountSidebar()
    const deleteButton = host?.querySelector<HTMLButtonElement>('.session-delete')
    deleteButton?.click()
    await vi.waitFor(() => expect(host?.querySelector('.confirm-dialog')).not.toBeNull())

    await vi.waitFor(() => expect(document.activeElement).toBe(
      host?.querySelector('.confirm-dialog .secondary-button'),
    ))
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await nextTick()
    expect(host?.querySelector('.confirm-dialog')).toBeNull()
    expect(document.activeElement).toBe(deleteButton)

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(events).toContain('close')
  })

  it('offers retry controls for session list and history failures', async () => {
    const { workspace } = mountSidebar()
    workspace.sessionsError.value = '会话列表暂时无法加载，请重试。'
    await nextTick()
    serviceMocks.fetchSessions.mockResolvedValue(sessions)
    host?.querySelector<HTMLButtonElement>('.session-error button')?.click()
    await vi.waitFor(() => expect(workspace.sessionsError.value).toBeNull())

    workspace.historyError.value = '会话内容暂时无法加载，请重试。'
    workspace.historyErrorSessionId.value = 'session-2'
    serviceMocks.fetchHistory.mockResolvedValue([])
    await nextTick()
    host?.querySelector<HTMLButtonElement>('.session-error button')?.click()
    await vi.waitFor(() => expect(workspace.historyError.value).toBeNull())
    expect(workspace.sessionId.value).toBe('session-2')
  })

  it('clears a stale history error when the current session is selected', async () => {
    const { workspace, events } = mountSidebar()
    workspace.historyError.value = '会话内容暂时无法加载，请重试。'
    workspace.historyErrorSessionId.value = 'session-2'
    await nextTick()

    await workspace.openSession('session-1')
    await nextTick()
    expect(workspace.historyError.value).toBeNull()

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(events).toContain('close')
  })

  it('keeps Tab focus inside the delete dialog', async () => {
    mountSidebar()
    host?.querySelector<HTMLButtonElement>('.session-delete')?.click()
    await vi.waitFor(() => expect(host?.querySelector('.confirm-dialog')).not.toBeNull())
    await vi.waitFor(() => expect(document.activeElement).toBe(
      host?.querySelector('.confirm-dialog .secondary-button'),
    ))

    const confirm = host?.querySelector<HTMLButtonElement>('.danger-button')
    confirm?.focus()
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true }))
    await nextTick()

    expect(document.activeElement).toBe(host?.querySelector('.confirm-dialog .secondary-button'))
  })

  it('closes the delete dialog after a successful deletion', async () => {
    const { workspace } = mountSidebar()
    serviceMocks.deleteSession.mockResolvedValue({ message: 'ok' })

    host?.querySelector<HTMLButtonElement>('.session-delete')?.click()
    await vi.waitFor(() => expect(host?.querySelector('.confirm-dialog')).not.toBeNull())
    host?.querySelector<HTMLButtonElement>('.danger-button')?.click()

    await vi.waitFor(() => expect(host?.querySelector('.confirm-dialog')).toBeNull())
    expect(workspace.sessions.value).toEqual([sessions[1]])
  })

  it('makes a closed mobile sidebar inert while keeping desktop navigation available', async () => {
    const originalWidth = window.innerWidth
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 375 })
    try {
      const { state } = mountSidebar(false)
      await nextTick()

      const sidebar = host?.querySelector<HTMLElement>('.app-sidebar')
      expect(sidebar?.hasAttribute('inert')).toBe(true)
      expect(sidebar?.getAttribute('aria-hidden')).toBe('true')

      state.open = true
      await nextTick()
      expect(sidebar?.hasAttribute('inert')).toBe(false)
    expect(sidebar?.hasAttribute('aria-hidden')).toBe(false)
    } finally {
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: originalWidth })
    }
  })
})
