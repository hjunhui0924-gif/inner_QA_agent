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
import FailureNotice from '../components/FailureNotice.vue'
import { useWorkspace } from '../composables/useWorkspace'
import { useEvidenceSelection } from '../composables/useEvidenceSelection'
import type { UiMessage } from '../types/api'
import { renderMarkdown } from '../utils/markdown'
import { hasOpenModal } from '../utils/overlayStack'
import { copyText } from '../utils/clipboard'

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

  sessionId,
} = useWorkspace()
const evidenceOpen = ref(false)
const evidenceModal = ref(false)
const {
  selection,
  select: selectEvidence,
  clear: clearEvidence,
} = useEvidenceSelection()
const composing = ref(false)
const evidenceContext = computed(() => {
  const message = messages.value.find(
    (item) => (item.message_id || item.id) === selection.value?.messageId,
  )
  return message
    ? `对应回答：${message.content.replace(/\s+/g, ' ').slice(0, 72)}`
    : ''
})
const composer = ref<HTMLTextAreaElement | null>(null)
const conversation = ref<HTMLElement | null>(null)
const nearBottom = ref(true)
let restoringScroll = true
let restoreFrame = 0
const copiedMessage = ref<string | null>(null)
const copyError = ref('')
let copyTimer: ReturnType<typeof setTimeout> | undefined

function updateEvidenceViewport(): void {
  evidenceModal.value = !window.matchMedia('(min-width: 1280px)').matches
}

onMounted(() => {
  updateEvidenceViewport()
  window.addEventListener('resize', updateEvidenceViewport)
  void nextTick(restoreScroll)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', updateEvidenceViewport)
  clearTimeout(copyTimer)
  cancelAnimationFrame(restoreFrame)
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
  () => [messages.value.length, messages.value.at(-1)?.content],
  async () => {
    const follow = nearBottom.value && !restoringScroll && !loadingHistory.value
    await nextTick()
    if (follow && !restoringScroll) scrollToLatest()
  },
)

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
  }
  cancelAnimationFrame(restoreFrame)
  restoreFrame = requestAnimationFrame(() => {
    restoringScroll = false
  })
}

function trackScroll(): void {
  if (restoringScroll || loadingHistory.value) return
  const element = conversation.value
  if (element) {
    nearBottom.value =
      element.scrollHeight - element.scrollTop - element.clientHeight < 80
    currentView.value.scrollTop = element.scrollTop
  }
}

function scrollToLatest(): void {
  const element = conversation.value
  if (element)
    element.scrollTop = messages.value.length ? element.scrollHeight : 0
  nearBottom.value = true
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

async function usePrompt(prompt: string) {
  draft.value = prompt
  await nextTick()
  composer.value?.focus()
}

async function submit(event?: KeyboardEvent) {
  if (event?.isComposing || composing.value || !canSend.value) return
  const content = draft.value
  draft.value = ''
  nearBottom.value = true
  await sendMessage(content)
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
    if (old?.triggerElement?.isConnected) old.triggerElement.focus()
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
      </header>

      <div
        ref="conversation"
        class="conversation"
        aria-label="对话内容"
        tabindex="0"
        @scroll="trackScroll"
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

        <div v-else class="message-list">
          <article
            v-for="message in messages"
            :key="message.id"
            class="message"
            :data-message-id="message.message_id || message.id"
            tabindex="-1"
            :class="[message.role, message.state]"
          >
            <span class="message-label"
              ><el-icon v-if="message.role === 'assistant'" aria-hidden="true"
                ><Collection /></el-icon
              >{{ message.role === 'user' ? '你' : '知识助手' }}</span
            >
            <div
              v-if="message.content"
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
            <div
              v-else-if="message.answerState === 'validating'"
              class="answer-placeholder"
            >
              正在校验回答依据…
            </div>
            <div
              v-else-if="message.answerState === 'streaming'"
              class="answer-placeholder"
            >
              {{ agentSteps.length ? '正在处理你的问题…' : '正在连接服务…' }}
            </div>
            <div
              v-else-if="message.answerState === 'cancelled'"
              class="answer-placeholder"
            >
              本次回答已取消。
            </div>
            <div v-if="message.citations?.length" class="citation-row">
              <button type="button" @click="openCitations(message, $event)">
                查看 {{ message.citations.length }} 条引用
              </button>
            </div>
            <FailureNotice
              v-if="
                message.answerState === 'fallback' ||
                message.answerState === 'error'
              "
              :answer-state="message.answerState"
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
            />
            <div
              v-if="
                message.role === 'assistant' &&
                message.content &&
                message.state === 'complete'
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
            </div>
          </article>
        </div>
      </div>

      <div class="composer-wrap">
        <button
          v-if="!nearBottom && messages.length"
          class="jump-latest secondary-button"
          type="button"
          @click="scrollToLatest"
        >
          <el-icon aria-hidden="true"><ArrowDown /></el-icon> 回到最新回答
        </button>
        <p v-if="copyError" class="field-error" role="alert">{{ copyError }}</p>
        <AgentProgress
          :steps="agentSteps"
          :active="sending"
          :started-at="sendingStartedAt"
        />
        <form class="composer" @submit.prevent="submit()">
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
            :disabled="sending || loadingHistory"
            @compositionstart="composing = true"
            @compositionend="composing = false"
            @keydown.ctrl.enter.prevent="submit($event)"
            @keydown.meta.enter.prevent="submit($event)"
          />
          <div class="composer-footer">
            <div class="composer-tools">
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
              <span v-else class="composer-scope"
                ><el-icon aria-hidden="true"><Collection /></el-icon>
                企业知识库</span
              >
              <span class="composer-shortcut">Ctrl / ⌘ + Enter 发送</span>
            </div>
            <button
              v-if="sending"
              class="secondary-button"
              type="button"
              @click="cancelMessage"
            >
              取消请求
            </button>
            <button
              v-else
              class="primary-button"
              type="submit"
              :disabled="!canSend"
            >
              <el-icon aria-hidden="true"><ArrowUp /></el-icon> 发送
            </button>
          </div>
        </form>
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
      :modal="evidenceModal"
      :citations="selection?.citationsSnapshot ?? []"
      :context="evidenceContext"
      :selected-citation-id="selection?.citationId"
      @close="closeEvidence"
    />
  </div>
</template>
