import { createApp, defineComponent, h, nextTick, reactive } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import MessageItem from './MessageItem.vue'
import type { UiMessage } from '../types/api'

let app: ReturnType<typeof createApp> | null = null
let host: HTMLDivElement | null = null

function message(overrides: Partial<UiMessage> = {}): UiMessage {
  return {
    id: 'assistant-1',
    role: 'assistant',
    content: '已完成回答',
    state: 'complete',
    answerState: 'complete',
    citations: [],
    ...overrides,
  }
}

function mountItem(initial: UiMessage) {
  const state = reactive({ item: initial, sending: false, copyState: 'idle' as const })
  host = document.createElement('div')
  document.body.appendChild(host)
  const Root = defineComponent({
    setup() {
      return () => h(MessageItem, {
        message: state.item,
        expanded: false,
        sending: state.sending,
        active: true,
        agentSteps: [],
        sendingStartedAt: 0,
        copyState: state.copyState,
      })
    },
  })
  app = createApp(Root)
  app.component('el-icon', defineComponent({
    setup(_, { slots }) {
      return () => h('span', slots.default?.())
    },
  }))
  app.mount(host)
  return state
}

afterEach(() => {
  vi.useRealTimers()
  app?.unmount()
  host?.remove()
  app = null
  host = null
})

describe('MessageItem', () => {
  it('keeps completed markdown stable when unrelated streaming state changes', async () => {
    const markdown = await import('../utils/markdown')
    const renderSpy = vi.spyOn(markdown, 'renderMarkdown')
    const state = mountItem(message())
    expect(renderSpy).toHaveBeenCalledTimes(1)

    state.sending = true
    await nextTick()
    expect(renderSpy).toHaveBeenCalledTimes(1)
  })

  it('throttles preview html and clears its timer on unmount', async () => {
    vi.useFakeTimers()
    const state = mountItem(message({
      content: '',
      state: 'streaming',
      answerState: 'streaming',
      previewContent: '第一段',
    }))

    await nextTick()
    const preview = () => host?.querySelector('.answer-preview .message-content')?.textContent || ''
    expect(preview()).toBe('')
    vi.advanceTimersByTime(50)
    await nextTick()
    expect(preview()).toContain('第一段')

    state.item.previewContent = '第二段'
    await nextTick()
    expect(preview()).toContain('第一段')
    app?.unmount()
    vi.advanceTimersByTime(100)
    expect(preview()).toBe('')
  })
})
