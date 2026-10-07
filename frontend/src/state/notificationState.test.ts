import { afterEach, describe, expect, it, vi } from 'vitest'

import { createNotificationState } from './notificationState'

afterEach(() => vi.useRealTimers())

describe('notification state', () => {
  it('keeps dismissal timers bounded and supports explicit retry feedback', () => {
    vi.useFakeTimers()
    const state = createNotificationState(() => 'toast-1')
    state.notify('error', '上传失败', '请重试')
    expect(state.toasts.value).toEqual([
      { id: 'toast-1', tone: 'error', title: '上传失败', detail: '请重试' },
    ])

    state.dismissToast('toast-1')
    expect(state.toasts.value).toHaveLength(0)
    vi.advanceTimersByTime(5000)
    expect(state.toasts.value).toHaveLength(0)
  })
})
