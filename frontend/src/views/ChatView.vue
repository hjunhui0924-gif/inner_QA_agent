<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  Connection,
  Collection,
  ArrowUp,
  ArrowDown,
  Search,
  Document,
  CopyDocument,
  Check,
  Right,
} from '@element-plus/icons-vue'

import AgentProgress from '../components/AgentProgress.vue'
import EvidencePanel from '../components/EvidencePanel.vue'
import ConversationNavigator from '../components/ConversationNavigator.vue'
import type { ConversationNavItem } from '../components/ConversationNavigator.vue'
import FailureNotice from '../components/FailureNotice.vue'
import ContextMeter from '../components/ContextMeter.vue'
import FeedbackDialog from '../components/FeedbackDialog.vue'
import { useWorkspace } from '../composables/useWorkspace'
import { useEvidenceSelection } from '../composables/useEvidenceSelection'
import { groupAttempts } from '../utils/attempts'
import type { UiMessage } from '../types/api'
import { renderMarkdown } from '../utils/markdown'
import { hasOpenModal } from '../utils/overlayStack'
import { copyText } from '../utils/clipboard'
import { ApiError, submitFeedback } from '../services/api'
import type { FeedbackCategory } from '../types/api'

const {
  messages,
  agentSteps,
  draft,
  currentView,
  initialize,
  activeSessionTitle,
  backendOnline,
  sending,
  sendingStartedAt,
  loadingHistory,
  sendMessage,
  chatMode,
  webSearchEnabled,
  cancelMessage,
  retryMessage,
  contextUsage,
  contextUsageLoading,
  contextUsageStale,

  sessionId,
} = useWorkspace()
const evidenceOpen = ref(false)
const evidenceModal = ref(false)
const {
  selection,
  select: selectEvidence,
  clear: clearEvidence,
} = useEvidenceSelection()
const expandedAttempts = ref(new Set<string>())
const attemptGroups = computed(() => groupAttempts(messages.value))
const collapsedIds = computed(() => new Set(attemptGroups.value.flatMap((group) =>
  expandedAttempts.value.has(group.id) ? [] : group.attempts.slice(0, -1).flat().map((message) => message.id),
)))
const attemptSummaries = computed(() => new Map(attemptGroups.value.filter((group) => group.attempts.length > 1)
  .map((group) => [group.attempts.at(-1)![0]!.id, group])))
const navigatorItems = computed<ConversationNavItem[]>(() => attemptGroups.value.map((group) => {
  const current = group.attempts.at(-1)?.[0]
  const messageId = current?.message_id || current?.id || group.id
  return {
    id: group.id,
    messageId,
    label: (current?.content || '未命名问题').replace(/\s+/g, ' ').trim(),
    attemptCount: group.attempts.length,
  }
}))
const activeNavigationId = ref<string | null>(null)
const navigationPaused = ref(false)
const navigationIdByMessage = computed(() => new Map(attemptGroups.value.flatMap((group) =>
  group.attempts.flatMap((attempt) => {
    const user = attempt[0]!
    return [[user.message_id || user.id, group.id] as const]
  }),
)))
function toggleAttempts(id: string) {
  navigationPaused.value = true
  const next = new Set(expandedAttempts.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  expandedAttempts.value = next
  void nextTick(updateActiveNavigation)
}
const composing = ref(false)
const editCandidate = ref<string | null>(null)
const evidenceContext = computed(() => {
  const message = messages.value.find(
    (item) => (item.message_id || item.id) === selection.value?.messageId,
  )
  if (!message) return ''
  const citations = selection.value?.citationsSnapshot ?? []
  const summary = message.content.replace(/\[C\d+\]/gi, (marker) => {
    const index = citations.findIndex((citation) => citation.citation_id.toUpperCase() === marker.slice(1, -1).toUpperCase())
    return index < 0 ? marker : `[${index + 1}]`
  })
  return `对应回答：${summary.replace(/\s+/g, ' ').slice(0, 72)}`
})
const composer = ref<HTMLTextAreaElement | null>(null)
const conversation = ref<HTMLElement | null>(null)
const messageList = ref<HTMLElement | null>(null)
const nearBottom = ref(true)
let readingAnchor: { node: HTMLElement; offset: number } | null = null
let layoutObserver: ResizeObserver | null = null
let layoutFrame = 0
let manualScrollIntent = false
let lastScrollTop = 0
let touchY: number | null = null
let restoringScroll = true
let restoreFrame = 0
const copiedMessage = ref<string | null>(null)
const copyError = ref('')
const feedbackMessage = ref<UiMessage | null>(null)
const feedbackTrigger = ref<HTMLElement | null>(null)
const feedbackCategory = ref<FeedbackCategory>('not_helpful')
const feedbackNote = ref('')
const feedbackSubmitting = ref(false)
const feedbackError = ref('')
const feedbackAttempt = ref<{ key: string; id: string } | null>(null)
let copyTimer: ReturnType<typeof setTimeout> | undefined

function updateEvidenceViewport(): void {
  evidenceModal.value = !window.matchMedia('(min-width: 1280px)').matches
}

onMounted(() => {
  updateEvidenceViewport()
  window.addEventListener('resize', updateEvidenceViewport)
  window.addEventListener('resize', resizeComposer)
  void nextTick(restoreScroll)
  void nextTick(resizeComposer)
  layoutObserver = new ResizeObserver(scheduleLayout)
  if (conversation.value) layoutObserver.observe(conversation.value)
  if (messageList.value) layoutObserver.observe(messageList.value)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', updateEvidenceViewport)
  window.removeEventListener('resize', resizeComposer)
  clearTimeout(copyTimer)
  cancelAnimationFrame(restoreFrame)
  cancelAnimationFrame(layoutFrame)
  layoutObserver?.disconnect()
  if (!loadingHistory.value && !restoringScroll)
    currentView.value.scrollTop =
      conversation.value?.scrollTop ?? currentView.value.scrollTop
  clearEvidence()
})

const prompts = computed(() =>
  chatMode.value === 'knowledge'
    ? [
        {
          title: '弄清审批流程',
          text: '差旅报销需要经过哪些审批？',
          icon: Connection,
        },
        {
          title: '找到制度依据',
          text: '合同归档需要保留哪些材料？',
          icon: Document,
        },
        {
          title: '确认具体规则',
          text: '差旅报销超过 5000 元需要谁审批？',
          icon: Search,
        },
      ]
    : [
        {
          title: '整理工作思路',
          text: '帮我整理一份产品发布会清单。',
          icon: Collection,
        },
        {
          title: '把概念讲清楚',
          text: '用一个简单例子解释什么是项目里程碑。',
          icon: Connection,
        },
        {
          title: '写得更清晰',
          text: '帮我拟一封邀请同事参加项目复盘的邮件。',
          icon: Document,
        },
      ],
)
const canSend = computed(
  () =>
    draft.value.trim().length > 0 &&
    draft.value.length <= 12000 &&
    !sending.value &&
    !loadingHistory.value &&
    !composing.value,
)
const modeLocked = computed(
  () => messages.value.length > 0 || loadingHistory.value,
)
const modes = [
  {
    id: 'knowledge' as const,
    label: '知识库',
    description: '只基于企业知识库回答',
    icon: Collection,
  },
  {
    id: 'general' as const,
    label: '通用模式',
    description: '开放问答，可连接互联网',
    icon: Connection,
  },
]

watch(
  () => [messages.value.length, messages.value.at(-1)?.content, messages.value.at(-1)?.answerState, messages.value.at(-1)?.previewContent],
  async () => {
    await nextTick()
    scheduleLayout()
  },
)
watch(messageList, (element, previous) => {
  if (previous) layoutObserver?.unobserve(previous)
  if (element) layoutObserver?.observe(element)
})

function rememberReadingAnchor(): void {
  const container = conversation.value
  if (!container || (nearBottom.value && !navigationPaused.value)) { readingAnchor = null; return }
  const top = container.getBoundingClientRect().top
  const candidates = [...container.querySelectorAll<HTMLElement>('.message.user, .message-content > *, .answer-preview .message-content > *')]
  const node = candidates.find((node) => node.getBoundingClientRect().top >= top && node.getBoundingClientRect().top < top + container.clientHeight)
    || candidates.find((node) => node.getBoundingClientRect().bottom > top)
  readingAnchor = node ? { node, offset: node.getBoundingClientRect().top - top } : null
}

function scheduleLayout(): void {
  cancelAnimationFrame(layoutFrame)
  layoutFrame = requestAnimationFrame(() => {
    const container = conversation.value
    if (!container || restoringScroll || loadingHistory.value) return
    // Geometry can create a bottom gap before ResizeObserver runs. Following
    // depends on user intent, not a gap measured during that reflow.
    if (!navigationPaused.value) scrollToLatest()
    else if (readingAnchor?.node.isConnected && container.contains(readingAnchor.node)) {
      const offset = readingAnchor.node.getBoundingClientRect().top - container.getBoundingClientRect().top
      if (Math.abs(offset - readingAnchor.offset) > 0.5) container.scrollTop += offset - readingAnchor.offset
      currentView.value.scrollTop = container.scrollTop
      lastScrollTop = container.scrollTop
      updateActiveNavigation()
    }
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
  // A native End animation keeps its old bottom target when streaming grows
  // the content. Use the same immediate resume action as the latest button.
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
function beginTouchScroll(event: TouchEvent): void { touchY = event.touches[0]?.clientY ?? null }
function pointerScrollIntent(event: PointerEvent): void {
  if (event.target === conversation.value) { manualScrollIntent = true; pauseFollowing() }
}

watch(
  loadingHistory,
  (loading) => {
    if (loading) restoringScroll = true
    else void nextTick(restoreScroll)
  },
  { flush: 'sync' },
)

watch(
  sessionId,
  () => {
    restoringScroll = true
    nearBottom.value = false
    navigationPaused.value = false
    readingAnchor = null
    manualScrollIntent = false
    lastScrollTop = 0
    activeNavigationId.value = null
    expandedAttempts.value = new Set()
    editCandidate.value = null
    feedbackMessage.value = null
    feedbackAttempt.value = null
    feedbackError.value = ''
    evidenceOpen.value = false
    clearEvidence()
    void nextTick(restoreScroll)
  },
  { flush: 'sync' },
)

function restoreScroll() {
  const element = conversation.value
  if (element) {
    element.scrollTop = currentView.value.scrollTop
    nearBottom.value =
      element.scrollHeight - element.scrollTop - element.clientHeight < 80
    navigationPaused.value = element.scrollHeight - element.scrollTop - element.clientHeight >= 3
    lastScrollTop = element.scrollTop
  }
  cancelAnimationFrame(restoreFrame)
  restoreFrame = requestAnimationFrame(() => {
    restoringScroll = false
    rememberReadingAnchor()
    updateActiveNavigation()
  })
}

function trackScroll(): void {
  if (restoringScroll || loadingHistory.value) return
  const element = conversation.value
  if (element) {
    const gap = element.scrollHeight - element.scrollTop - element.clientHeight
    if (element.scrollTop < lastScrollTop - 0.5 && gap >= 3) navigationPaused.value = true
    nearBottom.value =
      gap < 80
    if (manualScrollIntent && gap < 3) navigationPaused.value = false
    lastScrollTop = element.scrollTop
    currentView.value.scrollTop = element.scrollTop
    rememberReadingAnchor()
    updateActiveNavigation()
  }
}

function updateActiveNavigation(): void {
  const element = conversation.value
  if (!element || !navigatorItems.value.length) {
    activeNavigationId.value = null
    return
  }
  const candidates = [...element.querySelectorAll<HTMLElement>('.message.user')]
    .filter((item) => item.offsetParent !== null)
  const threshold = element.getBoundingClientRect().top + 96
  const current = candidates
    .filter((item) => item.getBoundingClientRect().top <= threshold)
    .at(-1) || candidates[0]
  activeNavigationId.value = navigationIdByMessage.value.get(current?.dataset.messageId || '') || null
}

async function navigateToQuestion(id: string): Promise<void> {
  const item = navigatorItems.value.find((item) => item.id === id)
  if (!item) return
  const requestedSession = sessionId.value
  manualScrollIntent = false
  navigationPaused.value = true
  nearBottom.value = false
  await nextTick()
  const target = [...(conversation.value?.querySelectorAll<HTMLElement>('.message.user') || [])]
    .find((target) => target.dataset.messageId === item.messageId)
  if (!target || !conversation.value || sessionId.value !== requestedSession) return
  const container = conversation.value
  const top = container.scrollTop + target.getBoundingClientRect().top - container.getBoundingClientRect().top - 24
  const behavior = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'
  target.focus({ preventScroll: true })
  container.scrollTo({ top: Math.max(0, top), behavior })
  currentView.value.scrollTop = container.scrollTop
  activeNavigationId.value = id
}

function scrollToLatest(): void {
  navigationPaused.value = false
  manualScrollIntent = false
  readingAnchor = null
  const element = conversation.value
  if (element)
    element.scrollTop = messages.value.length ? element.scrollHeight : 0
  nearBottom.value = true
  if (element) currentView.value.scrollTop = element.scrollTop
  if (element) lastScrollTop = element.scrollTop
  updateActiveNavigation()
}

async function copyAnswer(id: string, content: string): Promise<void> {
  copyError.value = ''
  try {
    await copyText(content)
    copiedMessage.value = id
    clearTimeout(copyTimer)
    copyTimer = setTimeout(() => {
      copiedMessage.value = null
    }, 1800)
  } catch {
    copyError.value = '复制失败，请选择回答文字后复制。'
  }
}

function openFeedback(message: UiMessage, event: Event): void {
  feedbackTrigger.value = event.currentTarget instanceof HTMLElement ? event.currentTarget : null
  feedbackMessage.value = message
  feedbackCategory.value = 'not_helpful'
  feedbackNote.value = ''
  feedbackError.value = ''
  feedbackAttempt.value = null
}

async function sendFeedback(): Promise<void> {
  const message = feedbackMessage.value
  if (!message?.turn_id || !message.message_id || feedbackSubmitting.value) return
  feedbackSubmitting.value = true
  feedbackError.value = ''
  const payload = { session_id: sessionId.value, turn_id: message.turn_id, message_id: message.message_id,
    category: feedbackCategory.value, note: feedbackNote.value }
  const key = JSON.stringify(payload)
  let attempt = feedbackAttempt.value
  if (!attempt || attempt.key !== key) {
    feedbackAttempt.value = { key, id: typeof crypto?.randomUUID === 'function' ? crypto.randomUUID()
      : `feedback-${Date.now()}-${Math.random().toString(36).slice(2)}` }
    attempt = feedbackAttempt.value
  }
  const current = () => feedbackAttempt.value === attempt && feedbackMessage.value === message
    && sessionId.value === payload.session_id
  try {
    await submitFeedback({ feedback_id: attempt.id, ...payload })
    if (current() && feedbackCategory.value === payload.category && feedbackNote.value === payload.note) {
      feedbackMessage.value = null
      feedbackAttempt.value = null
    }
  } catch (failure) {
    if (current()) feedbackError.value = failure instanceof ApiError && failure.status === 409
      ? '本次反馈与已有记录冲突，请修改反馈内容后重新提交。' : '反馈提交失败，请稍后重试。'
  } finally {
    feedbackSubmitting.value = false
  }
}

async function usePrompt(prompt: string) {
  draft.value = prompt
  await nextTick()
  composer.value?.focus()
}

async function submit(event?: KeyboardEvent) {
  if (event?.isComposing || event?.repeat || composing.value || !canSend.value) return
  const content = draft.value
  draft.value = ''
  nearBottom.value = true
  navigationPaused.value = false
  await sendMessage(content)
}

function resizeComposer(): void {
  const field = composer.value
  if (!field) return
  field.style.height = 'auto'
  const maxHeight = Math.min(240, Math.floor(window.innerHeight * 0.3))
  field.style.height = `${Math.min(Math.max(field.scrollHeight, 52), maxHeight)}px`
  field.style.overflowY = field.scrollHeight > maxHeight ? 'auto' : 'hidden'
}

function handleComposerKeydown(event: KeyboardEvent): void {
  if (event.repeat && (event.ctrlKey || event.metaKey) && event.key === 'Enter') {
    event.preventDefault()
  }
}

watch(() => draft.value, () => void nextTick(resizeComposer))
watch(() => sessionId.value, () => void nextTick(resizeComposer))

function editQuestion(message: UiMessage) {
  const index = messages.value.findIndex((item) => item.id === message.id)
  const question = messages.value[index - 1]?.content
  if (!question || sending.value) return
  if (draft.value.trim() && draft.value !== question) {
    editCandidate.value = question
    return
  }
  draft.value = question
  void nextTick(() => composer.value?.focus())
}

function replaceDraft() {
  if (editCandidate.value === null) return
  draft.value = editCandidate.value
  editCandidate.value = null
  void nextTick(() => composer.value?.focus())
}

function openCitations(message: UiMessage, event: Event, citationId?: string) {
  selectEvidence(
    sessionId.value,
    message,
    event.currentTarget as HTMLElement,
    citationId,
  )
  evidenceOpen.value = Boolean(selection.value)
}

function closeEvidence() {
  const old = selection.value
  evidenceOpen.value = false
  clearEvidence()
  void nextTick(() => {
    if (old?.triggerElement?.isConnected) old.triggerElement.focus({ preventScroll: true })
    else {
      const message = Array.from(
        document.querySelectorAll<HTMLElement>('[data-message-id]'),
      ).find((el) => el.dataset.messageId === old?.messageId)
      ;(message ?? composer.value)?.focus()
    }
  })
}

function handleMessageContentClick(event: MouseEvent, message: UiMessage) {
  const target = (event.target as HTMLElement).closest<HTMLButtonElement>(
    '[data-citation-id]',
  )
  if (!target || !(event.currentTarget as HTMLElement).contains(target)) return
  selectEvidence(sessionId.value, message, target, target.dataset.citationId)
  evidenceOpen.value = Boolean(selection.value)
}

function selectMode(mode: 'knowledge' | 'general') {
  if (sending.value || modeLocked.value) return
  chatMode.value = mode
}

function processSteps(message: UiMessage) {
  return (message.processSummary?.steps ?? []).map((step) => ({
    node: step.node,
    content: '',
    status: step.status,
    durationMs: step.duration_ms,
  }))
}
</script>

<template>
  <div
    class="chat-layout"
    :class="{ 'evidence-visible': evidenceOpen, 'is-empty': !messages.length }"
  >
    <section
      class="chat-surface"
      :aria-hidden="hasOpenModal ? 'true' : undefined"
      :inert="hasOpenModal ? true : undefined"
    >
      <header class="workspace-header">
        <div>
          <h1>{{ activeSessionTitle }}</h1>
          <span class="workspace-context">{{
            chatMode === 'knowledge' ? '企业知识问答' : '通用助手'
          }}</span>
        </div>
        <ConversationNavigator
          :key="sessionId"
          :items="navigatorItems"
          :active-id="activeNavigationId"
          :has-older-position="(!nearBottom || navigationPaused) && messages.length > 0"
          @navigate="navigateToQuestion"
          @latest="scrollToLatest"
        />
      </header>

      <div
        ref="conversation"
        class="conversation"
        aria-label="对话内容"
        tabindex="0"
        @scroll="trackScroll"
        @wheel.passive="wheelIntent"
        @keydown="scrollKeyIntent"
        @touchstart.passive="beginTouchScroll"
        @touchmove.passive="touchScrollIntent"
        @pointerdown="pointerScrollIntent"
      >
        <div v-if="loadingHistory" class="view-loading" role="status">
          正在加载会话记录…
        </div>
        <div v-if="messages.length === 0" class="chat-empty">
          <h2>
            {{
              chatMode === 'knowledge'
                ? '今天有什么工作问题？'
                : '从一个问题，打开思路。'
            }}
          </h2>
          <p class="welcome-description">{{ chatMode === 'knowledge' ? '查制度、找流程，让每个工作问题有据可循。' : '整理想法、梳理计划，从这里开始。' }}</p>
          <div v-if="!backendOnline" class="offline-banner">
            暂时无法连接知识服务。<button
              type="button"
              class="text-button"
              @click="initialize('chat')"
            >
              重试连接
            </button>
          </div>
        </div>

        <div v-else ref="messageList" class="message-list">
          <article
            v-for="message in messages"
            :key="message.id"
            v-show="!collapsedIds.has(message.id)"
            class="message"
            :data-message-id="message.message_id || message.id"
            tabindex="-1"
            :class="[message.role, message.state]"
          >
            <button v-if="attemptSummaries.has(message.id)" type="button" class="text-button attempt-toggle"
              :aria-expanded="expandedAttempts.has(attemptSummaries.get(message.id)!.id)"
              @click="toggleAttempts(attemptSummaries.get(message.id)!.id)">
              {{ expandedAttempts.has(attemptSummaries.get(message.id)!.id) ? '收起' : '查看' }}此前 {{ attemptSummaries.get(message.id)!.attempts.length - 1 }} 次尝试
            </button>
            <span class="message-label"
              ><el-icon v-if="message.role === 'assistant'" aria-hidden="true"
                ><Collection /></el-icon
              >{{ message.role === 'user' ? '你' : '知识助手' }}</span
            >
            <div
              v-if="message.content && message.answerState !== 'fallback' && message.answerState !== 'error'"
              class="message-content"
              v-html="
                renderMarkdown(
                  message.content,
                  (message.citations ?? []).map(
                    (citation) => citation.citation_id,
                  ),
                )
              "
              @click="handleMessageContentClick($event, message)"
            />
            <div v-if="message.previewContent && message.answerState !== 'complete' && message.answerState !== 'fallback'" class="answer-preview">
              <small class="preview-label">{{ ['cancelled', 'error'].includes(message.answerState || '') ? '未完成草稿 · 本次回答未完成' : '生成中 · 尚未完成' }}</small>
              <div class="message-content" v-html="renderMarkdown(message.previewContent, [])" />
            </div>
            <div v-if="message.role === 'assistant' && sending && message.id === messages.at(-1)?.id" class="answer-wait">
              <AgentProgress :steps="agentSteps" :active="true" :started-at="sendingStartedAt" />
              <button type="button" class="text-button" @click="cancelMessage">取消请求</button>
            </div>
            <AgentProgress
              v-else-if="message.role === 'assistant' && message.processSummary?.steps?.length"
              :steps="processSteps(message)"
              :active="false"
              :final-status="message.processSummary.status"
              :elapsed-ms="message.processSummary.elapsed_ms"
            />
            <div v-if="message.answerState === 'cancelled'" class="answer-placeholder">
              本次回答已取消。
              <div class="answer-actions">
                <button type="button" class="secondary-button" :disabled="sending" @click="retryMessage(message)">重新生成</button>
                <button type="button" class="text-button" :disabled="sending" @click="editQuestion(message)">编辑问题</button>
              </div>
            </div>
            <div v-if="message.citations?.length" class="citation-row">
              <p class="reference-heading">参考来源</p>
              <ol class="reference-list">
                <li v-for="(citation, index) in message.citations" :key="citation.citation_id">
                  <button type="button" @click="openCitations(message, $event, citation.citation_id)">
                    <span class="reference-number">[{{ index + 1 }}]</span>
                    <span>{{ citation.title || '未命名文档' }}<span v-if="citation.page"> · 第 {{ citation.page }} 页</span></span>
                  </button>
                </li>
              </ol>
            </div>
            <FailureNotice
              v-if="
                message.answerState === 'fallback' ||
                message.answerState === 'error'
              "
              :answer-state="message.answerState"
              :busy="sending"
              :failure-type="message.failureType"
              :failure-stage="message.failureStage"
              :trace-id="message.traceId"
              :retryable="
                message.answerState === 'error' ||
                [
                  'generation_error',
                  'citation_error',
                  'search_error',
                  'search_answer_error',
                ].includes(message.failureType || '')
              "
              @retry="retryMessage(message)"
              @edit="editQuestion(message)"
              @without-web="retryMessage(message, { withoutWeb: true })"
            />
            <div
              v-if="
                message.role === 'assistant' &&
                message.content &&
                message.answerState === 'complete'
              "
              class="answer-actions"
            >
              <button
                type="button"
                class="text-button"
                @click="copyAnswer(message.id, message.content)"
              >
                <el-icon aria-hidden="true"
                  ><Check v-if="copiedMessage === message.id" /><CopyDocument
                    v-else
                /></el-icon>
                {{ copiedMessage === message.id ? '已复制回答' : '复制回答' }}
              </button>
              <button type="button" class="text-button" @click="openFeedback(message, $event)">反馈回答</button>
            </div>
          </article>
        </div>
      </div>

      <div class="composer-wrap">
        <button
          v-if="(!nearBottom || navigationPaused) && messages.length"
          class="jump-latest secondary-button"
          type="button"
          @click="scrollToLatest"
        >
          <el-icon aria-hidden="true"><ArrowDown /></el-icon> 回到最新回答
        </button>
        <p v-if="copyError" class="field-error" role="alert">{{ copyError }}</p>
        <div v-if="editCandidate !== null" class="draft-confirm" role="status">
          输入框已有草稿，是否替换为原问题？
          <button type="button" class="text-button" @click="replaceDraft">替换草稿</button>
          <button type="button" class="text-button" @click="editCandidate = null">保留草稿</button>
        </div>
        <form class="composer" @submit.prevent="submit()">
          <label for="question" class="sr-only">{{
            chatMode === 'knowledge' ? '向企业知识库提问' : '向通用助手提问'
          }}</label>
          <textarea
            id="question"
            ref="composer"
            name="question"
            autocomplete="off"
            v-model="draft"
            rows="2"
            maxlength="12000"
            :placeholder="
              chatMode === 'knowledge'
                ? '例如：差旅报销超过 5000 元需要谁审批？'
                : '例如：帮我整理一份产品发布会清单。'
            "
            :disabled="loadingHistory"
            @compositionstart="composing = true"
            @compositionend="composing = false"
            @input="resizeComposer"
            @keydown="handleComposerKeydown"
            @keydown.ctrl.enter.prevent="submit($event)"
            @keydown.meta.enter.prevent="submit($event)"
          />
          <div class="composer-footer">
            <div class="composer-tools">
              <div
                v-if="!modeLocked"
                class="mode-switcher"
                role="group"
                aria-label="对话模式"
              >
                <button
                  v-for="mode in modes"
                  :key="mode.id"
                  class="mode-option"
                  :class="{ active: chatMode === mode.id }"
                  type="button"
                  :aria-pressed="chatMode === mode.id"
                  :title="mode.description"
                  :disabled="sending"
                  @click="selectMode(mode.id)"
                >
                  <el-icon aria-hidden="true"
                    ><component :is="mode.icon"
                  /></el-icon>
                  <span>{{ mode.label }}</span>
                </button>
              </div>

              <button
                v-if="chatMode === 'general'"
                type="button"
                class="search-toggle"
                :class="{ active: webSearchEnabled }"
                :disabled="sending"
                @click="webSearchEnabled = !webSearchEnabled"
                :aria-pressed="webSearchEnabled"
              >
                <el-icon aria-hidden="true"><Search /></el-icon> 联网搜索
              </button>
              <span v-else-if="modeLocked" class="composer-scope"
                ><el-icon aria-hidden="true"><Collection /></el-icon>
                企业知识库</span
              >
            </div>
            <button
              v-if="sending"
              class="composer-submit is-stop"
              type="button"
              aria-label="停止回答"
              title="停止回答"
              @click="cancelMessage"
            >
              <span class="stop-symbol" aria-hidden="true" />
            </button>
            <button
              v-else
              class="composer-submit"
              type="submit"
              aria-label="发送"
              title="发送（Ctrl / ⌘ + Enter）"
              :disabled="!canSend"
            >
              <el-icon aria-hidden="true"><ArrowUp /></el-icon>
            </button>
          </div>
        </form>
        <div class="composer-meta">
          <span class="composer-meta-left"><ContextMeter :usage="contextUsage" :loading="contextUsageLoading" :stale="contextUsageStale" />{{ chatMode === 'knowledge' ? '基于企业知识库 · 附参考来源' : (webSearchEnabled ? '通用助手 · 联网搜索已开启' : '通用助手') }}</span>
          <span class="composer-shortcut">{{ sending ? '可先编辑下一条问题' : 'Ctrl / ⌘ + Enter 发送' }}</span>
        </div>
        <div v-if="!messages.length" class="prompt-grid">
          <button
            v-for="prompt in prompts"
            :key="prompt.title"
            type="button"
            :disabled="sending"
            @click="usePrompt(prompt.text)"
          >
            <el-icon class="prompt-icon" aria-hidden="true"
              ><component :is="prompt.icon"
            /></el-icon>
            <strong>{{ prompt.title }}</strong>
            <span>{{ prompt.text }}</span>
            <el-icon class="prompt-action" aria-hidden="true"
              ><Right
            /></el-icon>
          </button>
        </div>
        <p v-if="draft.length >= 12000" class="field-error">
          已达到 12000 字上限，请缩短问题。
        </p>
        <p class="composer-note">
          {{
            chatMode === 'knowledge'
              ? '回答基于可访问的企业文档，重要事项请核对原文。'
              : '通用回答可能存在偏差，重要信息请进一步核实。'
          }}
        </p>
      </div>
    </section>

    <EvidencePanel
      :open="evidenceOpen"
      :blocked="Boolean(feedbackMessage)"
      :modal="evidenceModal"
      :citations="selection?.citationsSnapshot ?? []"
      :context="evidenceContext"
      :selected-citation-id="selection?.citationId"
      :session-id="selection?.sessionId"
      :turn-id="selection?.turnId"
      @close="closeEvidence"
    />
    <FeedbackDialog
      :open="Boolean(feedbackMessage)"
      v-model:category="feedbackCategory"
      v-model:note="feedbackNote"
      :submitting="feedbackSubmitting"
      :error="feedbackError"
      :return-focus-to="feedbackTrigger"
      @close="feedbackMessage = null"
      @submit="sendFeedback"
    />
  </div>
</template>
