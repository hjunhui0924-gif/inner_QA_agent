import { createApp, defineComponent, h, nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ConversationNavigator from './ConversationNavigator.vue'

let app: ReturnType<typeof createApp> | null = null
let host: HTMLDivElement | null = null

function mountNavigator(onNavigate = vi.fn()) {
  host = document.createElement('div')
  document.body.appendChild(host)
  const Root = defineComponent({
    setup() {
      return () => h(ConversationNavigator, {
        items: [
          { id: 'turn-1', messageId: 'turn-1', label: '第一条问题', attemptCount: 1 },
          { id: 'turn-2', messageId: 'turn-2', label: '第二条问题', attemptCount: 2 },
        ],
        activeId: 'turn-2',
        hasOlderPosition: true,
        onNavigate,
      })
    },
  })
  app = createApp(Root)
  app.mount(host)
}

afterEach(() => {
  app?.unmount()
  host?.remove()
  app = null
  host = null
})

describe('ConversationNavigator', () => {
  it('opens a keyboard accessible question list and marks the active item', async () => {
    mountNavigator()
    const toggle = host?.querySelector<HTMLButtonElement>('.conversation-navigator-toggle')
    expect(toggle?.getAttribute('aria-expanded')).toBe('false')
    toggle?.click()
    await nextTick()
    expect(toggle?.getAttribute('aria-expanded')).toBe('true')
    expect(host?.querySelectorAll('.conversation-navigator-item')).toHaveLength(2)
    expect(host?.querySelector('[aria-current="location"]')?.textContent).toContain('第二条问题')
    expect(host?.textContent).toContain('2 次尝试')
  })

  it('supports keyboard navigation, a full question preview, and Escape focus restoration', async () => {
    mountNavigator()
    const toggle = host!.querySelector<HTMLButtonElement>('.conversation-navigator-toggle')!
    toggle.click()
    await nextTick()
    await nextTick()
    const items = host!.querySelectorAll<HTMLButtonElement>('.conversation-navigator-item')
    expect(document.activeElement).toBe(items[1])
    items[1]!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Home', bubbles: true }))
    await nextTick()
    expect(document.activeElement).toBe(items[0])
    expect(host!.querySelector('.conversation-navigator-question-preview')?.textContent).toBe('第一条问题')
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await nextTick()
    expect(host!.querySelector('nav')).toBeNull()
    expect(document.activeElement).toBe(toggle)
  })

  it('emits the selected stable identity and dismisses the directory', async () => {
    const navigate = vi.fn()
    mountNavigator(navigate)
    host!.querySelector<HTMLButtonElement>('.conversation-navigator-toggle')!.click()
    await nextTick()
    host!.querySelector<HTMLButtonElement>('.conversation-navigator-item')!.click()
    await nextTick()
    expect(navigate).toHaveBeenCalledWith('turn-1')
    expect(host!.querySelector('nav')).toBeNull()
  })
})
