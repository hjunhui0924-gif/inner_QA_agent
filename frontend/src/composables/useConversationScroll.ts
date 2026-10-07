import { nextTick, onBeforeUnmount, onMounted, ref, watch, type ComputedRef, type Ref } from 'vue'

import type { UiMessage } from '../types/api'
import { recordMessageCounts, recordScrollLongTask } from '../utils/performance'

export interface ConversationNavigationItem {
  id: string
  messageId: string
}

interface ScrollOptions {
  conversation: Ref<HTMLElement | null>
  messageList: Ref<HTMLElement | null>
  messages: Ref<UiMessage[]>
  currentView: { value: { scrollTop: number } }
  loadingHistory: Ref<boolean>
  sessionId: Ref<string>
  navigationItems: ComputedRef<ConversationNavigationItem[]>
  navigationIdByMessage: ComputedRef<Map<string, string>>
}

type ReadingAnchor = { node: HTMLElement; offset: number }

export function useConversationScroll(options: ScrollOptions) {
  const nearBottom = ref(true)
  const navigationPaused = ref(false)
  const activeNavigationId = ref<string | null>(null)
  let readingAnchor: ReadingAnchor | null = null
  let layoutObserver: ResizeObserver | null = null
  let layoutFrame = 0
  let restoreFrame = 0
  let scrollFrame = 0
  let manualScrollIntent = false
  let lastScrollTop = 0
  let touchY: number | null = null
  let restoringScroll = true
  let scrollTracking = false
  const messageNodes = new Map<string, HTMLElement>()
  let anchorCandidates: HTMLElement[] = []

  function refreshMessageNodes(): void {
    messageNodes.clear()
    const list = options.messageList.value
    if (!list) {
      anchorCandidates = []
      return
    }
    const candidates: HTMLElement[] = []
    list.querySelectorAll<HTMLElement>('.message').forEach((message) => {
      const id = message.dataset.messageId
      if (id) messageNodes.set(id, message)
      candidates.push(message)
      message.querySelectorAll<HTMLElement>('.message-content > *, .answer-preview .message-content > *').forEach((node) => candidates.push(node))
    })
    anchorCandidates = candidates
    recordMessageCounts(options.messages.value.length, messageNodes.size)
  }

  function updateActiveNavigation(): void {
    const container = options.conversation.value
    if (!container || !options.navigationItems.value.length) {
      activeNavigationId.value = null
      return
    }
    const candidates = [...messageNodes.values()]
      .filter((item) => item.classList.contains('user') && item.offsetParent !== null)
    const threshold = container.getBoundingClientRect().top + 96
    const current = candidates.filter((item) => item.getBoundingClientRect().top <= threshold).at(-1) || candidates[0]
    activeNavigationId.value = options.navigationIdByMessage.value.get(current?.dataset.messageId || '') || null
  }

  function rememberReadingAnchor(): void {
    const container = options.conversation.value
    if (!container || (nearBottom.value && !navigationPaused.value)) {
      readingAnchor = null
      return
    }
    const top = container.getBoundingClientRect().top
    const node = anchorCandidates.find((candidate) => {
      const rect = candidate.getBoundingClientRect()
      return rect.top >= top && rect.top < top + container.clientHeight
    }) || anchorCandidates.find((candidate) => candidate.getBoundingClientRect().bottom > top)
    readingAnchor = node ? { node, offset: node.getBoundingClientRect().top - top } : null
  }

  function scheduleLayout(): void {
    cancelAnimationFrame(layoutFrame)
    layoutFrame = requestAnimationFrame(() => {
      const started = performance.now()
      const container = options.conversation.value
      if (!container || restoringScroll || options.loadingHistory.value) return
      if (!navigationPaused.value) scrollToLatest()
      else if (readingAnchor?.node.isConnected && container.contains(readingAnchor.node)) {
        const offset = readingAnchor.node.getBoundingClientRect().top - container.getBoundingClientRect().top
        if (Math.abs(offset - readingAnchor.offset) > 0.5) container.scrollTop += offset - readingAnchor.offset
        options.currentView.value.scrollTop = container.scrollTop
        lastScrollTop = container.scrollTop
        updateActiveNavigation()
      }
      recordScrollLongTask(performance.now() - started)
    })
  }

  function pauseFollowing(): void {
    navigationPaused.value = true
    rememberReadingAnchor()
  }

  function wheelIntent(event: WheelEvent): void {
    manualScrollIntent = true
    if (event.deltaY < 0) pauseFollowing()
  }

  function scrollKeyIntent(event: KeyboardEvent): void {
    if (event.target instanceof HTMLElement && event.target.closest('input, textarea, select')) return
    if (event.key === 'End') {
      event.preventDefault()
      scrollToLatest()
      return
    }
    if (['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '].includes(event.key)) manualScrollIntent = true
    if (['ArrowUp', 'PageUp', 'Home'].includes(event.key) || (event.key === ' ' && event.shiftKey)) pauseFollowing()
  }

  function touchScrollIntent(event: TouchEvent): void {
    const next = event.touches[0]?.clientY
    if (next != null && touchY != null && next > touchY) pauseFollowing()
    touchY = next ?? null
    manualScrollIntent = true
  }

  function beginTouchScroll(event: TouchEvent): void {
    touchY = event.touches[0]?.clientY ?? null
  }

  function pointerScrollIntent(event: PointerEvent): void {
    if (event.target === options.conversation.value) {
      manualScrollIntent = true
      pauseFollowing()
    }
  }

  function flushScroll(): void {
    scrollFrame = 0
    scrollTracking = false
    if (restoringScroll || options.loadingHistory.value) return
    const element = options.conversation.value
    if (!element) return
    const started = performance.now()
    const gap = element.scrollHeight - element.scrollTop - element.clientHeight
    if (element.scrollTop < lastScrollTop - 0.5 && gap >= 3) navigationPaused.value = true
    nearBottom.value = gap < 80
    if (manualScrollIntent && gap < 3) navigationPaused.value = false
    lastScrollTop = element.scrollTop
    options.currentView.value.scrollTop = element.scrollTop
    rememberReadingAnchor()
    updateActiveNavigation()
    recordScrollLongTask(performance.now() - started)
  }

  function trackScroll(): void {
    if (scrollTracking) return
    scrollTracking = true
    scrollFrame = requestAnimationFrame(flushScroll)
  }

  function restoreScroll(): void {
    const element = options.conversation.value
    if (element) {
      element.scrollTop = options.currentView.value.scrollTop
      nearBottom.value = element.scrollHeight - element.scrollTop - element.clientHeight < 80
      navigationPaused.value = element.scrollHeight - element.scrollTop - element.clientHeight >= 3
      lastScrollTop = element.scrollTop
    }
    cancelAnimationFrame(restoreFrame)
    restoreFrame = requestAnimationFrame(() => {
      restoringScroll = false
      refreshMessageNodes()
      rememberReadingAnchor()
      updateActiveNavigation()
    })
  }

  function scrollToLatest(): void {
    navigationPaused.value = false
    manualScrollIntent = false
    readingAnchor = null
    const element = options.conversation.value
    if (element) element.scrollTop = options.messages.value.length ? element.scrollHeight : 0
    nearBottom.value = true
    if (element) options.currentView.value.scrollTop = element.scrollTop
    if (element) lastScrollTop = element.scrollTop
    updateActiveNavigation()
  }

  async function navigateToQuestion(id: string): Promise<void> {
    const item = options.navigationItems.value.find((candidate) => candidate.id === id)
    if (!item) return
    const requestedSession = options.sessionId.value
    manualScrollIntent = false
    navigationPaused.value = true
    nearBottom.value = false
    await nextTick()
    const target = messageNodes.get(item.messageId)
    const container = options.conversation.value
    if (!target || !container || options.sessionId.value !== requestedSession) return
    const top = container.scrollTop + target.getBoundingClientRect().top - container.getBoundingClientRect().top - 24
    const behavior = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'
    target.focus({ preventScroll: true })
    container.scrollTo({ top: Math.max(0, top), behavior })
    options.currentView.value.scrollTop = container.scrollTop
    activeNavigationId.value = id
  }

  watch(
    () => [options.messages.value.length, options.messages.value.at(-1)?.content, options.messages.value.at(-1)?.answerState, options.messages.value.at(-1)?.previewContent],
    async () => {
      await nextTick()
      refreshMessageNodes()
      scheduleLayout()
    },
  )
  watch(
    () => options.messages.value.map((message) => message.id).join('\u001f'),
    async () => {
      await nextTick()
      refreshMessageNodes()
      updateActiveNavigation()
    },
  )
  watch(options.messageList, (element, previous) => {
    if (previous) layoutObserver?.unobserve(previous)
    if (element) layoutObserver?.observe(element)
    refreshMessageNodes()
  })
  watch(options.loadingHistory, (loading) => {
    if (loading) restoringScroll = true
    else void nextTick(restoreScroll)
  }, { flush: 'sync' })
  watch(options.sessionId, () => {
    restoringScroll = true
    nearBottom.value = false
    navigationPaused.value = false
    readingAnchor = null
    manualScrollIntent = false
    lastScrollTop = 0
    activeNavigationId.value = null
    void nextTick(restoreScroll)
  }, { flush: 'sync' })

  onMounted(() => {
    void nextTick(restoreScroll)
    layoutObserver = new ResizeObserver(scheduleLayout)
    if (options.conversation.value) layoutObserver.observe(options.conversation.value)
    if (options.messageList.value) layoutObserver.observe(options.messageList.value)
    refreshMessageNodes()
  })

  onBeforeUnmount(() => {
    cancelAnimationFrame(restoreFrame)
    cancelAnimationFrame(layoutFrame)
    cancelAnimationFrame(scrollFrame)
    layoutObserver?.disconnect()
    if (!options.loadingHistory.value && !restoringScroll)
      options.currentView.value.scrollTop = options.conversation.value?.scrollTop ?? options.currentView.value.scrollTop
  })

  return {
    nearBottom,
    navigationPaused,
    activeNavigationId,
    trackScroll,
    scrollToLatest,
    rememberReadingAnchor,
    restoreScroll,
    pauseFollowing,
    wheelIntent,
    scrollKeyIntent,
    touchScrollIntent,
    beginTouchScroll,
    pointerScrollIntent,
    navigateToQuestion,
    updateActiveNavigation,
    refreshMessageNodes,
  }
}
