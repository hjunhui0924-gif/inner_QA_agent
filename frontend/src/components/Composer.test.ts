import { createApp, defineComponent, h, nextTick, reactive } from 'vue'
import { afterEach, describe, expect, it } from 'vitest'

import Composer, { type ComposerMode, type ComposerPrompt } from './Composer.vue'
import type { ChatMode } from '../types/api'

let app: ReturnType<typeof createApp> | null = null
let host: HTMLDivElement | null = null

const icon = defineComponent({ setup: () => () => h('span') })
const modes: ComposerMode[] = [
  { id: 'knowledge', label: '知识库', description: '知识库', icon },
  { id: 'general', label: '通用模式', description: '通用', icon },
]
const prompts: ComposerPrompt[] = [{ title: '示例', text: '示例问题', icon }]

function mountComposer() {
  const state = reactive({ draft: '', chatMode: 'knowledge' as ChatMode, webSearchEnabled: false, sending: false, composing: false })
  const events: string[] = []
  host = document.createElement('div')
  document.body.appendChild(host)
  const Root = defineComponent({
    setup() {
      return () => h(Composer, {
        draft: state.draft,
        chatMode: state.chatMode,
        webSearchEnabled: state.webSearchEnabled,
        loadingHistory: false,
        sending: state.sending,
        composing: state.composing,
        canSend: state.draft.trim().length > 0 && !state.sending && !state.composing,
        modeLocked: false,
        messagesLength: 0,
        nearBottom: true,
        navigationPaused: false,
        editCandidate: null,
        copyError: '',
        contextUsage: null,
        contextUsageLoading: false,
        contextUsageStale: false,
        modes,
        prompts,
        'onUpdate:draft': (value: string) => { state.draft = value },
        'onUpdate:chatMode': (value: ChatMode) => { state.chatMode = value },
        'onUpdate:webSearchEnabled': (value: boolean) => { state.webSearchEnabled = value },
        onSubmit: () => events.push('submit'),
        onCompositionStart: () => { state.composing = true },
        onCompositionEnd: () => { state.composing = false },
        onUsePrompt: (value: string) => events.push(`prompt:${value}`),
      })
    },
  })
  app = createApp(Root)
  app.component('el-icon', defineComponent({
    setup(_, { slots }) { return () => h('span', slots.default?.()) },
  }))
  app.mount(host)
  return { state, events }
}

afterEach(() => {
  app?.unmount()
  host?.remove()
  app = null
  host = null
})

describe('Composer', () => {
  it('keeps composing input from submitting and sends with Ctrl+Enter after composition ends', async () => {
    const { state, events } = mountComposer()
    const textarea = host?.querySelector<HTMLTextAreaElement>('#question')
    if (!textarea) throw new Error('composer textarea not found')
    textarea.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true }))
    await nextTick()
    textarea.value = '中文问题'
    textarea.dispatchEvent(new Event('input', { bubbles: true }))
    textarea.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', ctrlKey: true, bubbles: true, cancelable: true }))
    expect(events).toEqual([])
    textarea.dispatchEvent(new CompositionEvent('compositionend', { bubbles: true }))
    await nextTick()
    expect(state.composing).toBe(false)
    textarea.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', ctrlKey: true, bubbles: true, cancelable: true }))
    expect(events).toEqual(['submit'])
  })

  it('emits a prompt action at the original interaction location', async () => {
    const { events } = mountComposer()
    host?.querySelector<HTMLButtonElement>('.prompt-grid button')?.click()
    await nextTick()
    expect(events).toEqual(['prompt:示例问题'])
  })
})
