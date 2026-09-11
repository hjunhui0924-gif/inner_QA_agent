import { createApp, defineComponent, h, nextTick } from 'vue'
import { afterEach, describe, expect, it } from 'vitest'

import AgentProgress from './AgentProgress.vue'
import type { AgentStep } from '../types/api'

let app: ReturnType<typeof createApp> | null = null
let host: HTMLDivElement | null = null

function mountProgress(steps: AgentStep[], active = true) {
  host = document.createElement('div')
  document.body.appendChild(host)
  const Root = defineComponent({
    setup() {
      return () => h(AgentProgress, { steps, active })
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

describe('AgentProgress', () => {
  it('keeps the detailed timeline collapsed while exposing the current step', () => {
    mountProgress([
      { node: 'retrieve', content: '正在检索知识库', status: 'complete' },
      { node: 'generate', content: '正在组织回答', status: 'running' },
      { node: 'check_hallucination', content: '正在校验回答依据', status: 'pending' },
    ])

    expect(host?.textContent).toContain('组织回答')
    const toggle = host?.querySelector<HTMLButtonElement>('.progress-toggle')
    expect(toggle?.textContent).toContain('查看处理过程')
    expect(toggle?.getAttribute('aria-expanded')).toBe('false')
    expect(host?.querySelector('.step-list')).toBeNull()
  })

  it('reveals status text and the timeline after the user expands it', async () => {
    mountProgress([
      { node: 'retrieve', content: '正在检索知识库' },
      { node: 'error', content: '内部错误详情', status: 'error' },
    ], false)

    host?.querySelector<HTMLButtonElement>('.progress-toggle')?.click()
    await nextTick()

    const toggle = host?.querySelector<HTMLButtonElement>('.progress-toggle')
    expect(toggle?.getAttribute('aria-expanded')).toBe('true')
    expect(host?.textContent).toContain('待处理')
    expect(host?.textContent).toContain('失败')
    expect(host?.textContent).not.toContain('内部错误详情')
    expect(host?.querySelector('.step-error')).not.toBeNull()
  })
})
