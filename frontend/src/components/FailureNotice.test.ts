import { createApp, defineComponent, h, nextTick, reactive } from 'vue'
import { afterEach, describe, expect, it } from 'vitest'

import FailureNotice from './FailureNotice.vue'

let app: ReturnType<typeof createApp> | null = null
let host: HTMLDivElement | null = null

function mountNotice(initial: {
  answerState: 'fallback' | 'error'
  failureType: string | null
  failureStage: string | null
  traceId: string | null
  retryable: boolean
}) {
  const props = reactive(initial)
  const events: string[] = []
  host = document.createElement('div')
  document.body.appendChild(host)
  const Root = defineComponent({
    setup() {
      return () => h(FailureNotice, {
        ...props,
        onRetry: () => events.push('retry'),
      })
    },
  })
  app = createApp(Root)
  app.mount(host)
  return { props, events }
}

afterEach(() => {
  app?.unmount()
  host?.remove()
  app = null
  host = null
})

describe('FailureNotice', () => {
  it('explains citation failures without exposing internal reasons', () => {
    mountNotice({
      answerState: 'fallback',
      failureType: 'citation_error',
      failureStage: 'citation',
      traceId: 'trace-citation',
      retryable: false,
    })

    expect(host?.textContent).toContain('回答引用校验未通过')
    expect(host?.textContent).toContain('系统没有提交未经验证的答案')
    expect(host?.textContent).not.toContain('citation')
    expect(host?.querySelector('.failure-retry')).toBeNull()
  })

  it('keeps compatibility with historical abstention failures', () => {
    mountNotice({
      answerState: 'fallback',
      failureType: 'abstention_error',
      failureStage: 'evidence',
      traceId: null,
      retryable: false,
    })

    expect(host?.textContent).toContain('当前资料不足以回答这个问题')
  })

  it('offers retry for an incomplete request and keeps technical details collapsed', async () => {
    const { events } = mountNotice({
      answerState: 'error',
      failureType: null,
      failureStage: 'runtime',
      traceId: 'trace-runtime',
      retryable: true,
    })

    const details = host?.querySelector<HTMLDetailsElement>('details')
    expect(details?.open).toBe(false)
    expect(host?.textContent).not.toContain('trace-runtime')

    host?.querySelector<HTMLButtonElement>('.failure-retry')?.click()
    await nextTick()

    expect(events).toEqual(['retry'])
    details?.querySelector('summary')?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await nextTick()
    expect(details?.open).toBe(true)
    expect(details?.textContent).toContain('trace-runtime')
  })
})
