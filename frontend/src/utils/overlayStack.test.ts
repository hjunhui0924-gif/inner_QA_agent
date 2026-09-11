import { afterEach, describe, expect, it, vi } from 'vitest'

import { registerOverlay } from './overlayStack'

const controllers: Array<ReturnType<typeof registerOverlay>> = []

afterEach(() => {
  controllers.splice(0).forEach((controller) => controller.unregister())
})
describe('overlayStack', () => {
  it('dispatches keyboard events only to the topmost open overlay', () => {
    const firstHandler = vi.fn()
    const secondHandler = vi.fn()
    const first = registerOverlay(firstHandler)
    const second = registerOverlay(secondHandler)
    controllers.push(first, second)

    first.setOpen(true)
    second.setOpen(true)
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))

    expect(firstHandler).not.toHaveBeenCalled()
    expect(secondHandler).toHaveBeenCalledTimes(1)
  })

  it('reveals the previous overlay when the topmost overlay closes', () => {
    const firstHandler = vi.fn()
    const secondHandler = vi.fn()
    const first = registerOverlay(firstHandler)
    const second = registerOverlay(secondHandler)
    controllers.push(first, second)

    first.setOpen(true)
    second.setOpen(true)
    second.setOpen(false)
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab' }))

    expect(firstHandler).toHaveBeenCalledTimes(1)
    expect(secondHandler).not.toHaveBeenCalled()
  })
})
