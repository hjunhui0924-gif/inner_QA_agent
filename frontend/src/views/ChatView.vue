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
import type { Citation } from '../types/api'
import { renderMarkdown } from '../utils/markdown'
import { copyText } from '../utils/clipboard'

const {
  messages,
  agentSteps,
  activeCitations,
  activeSessionTitle,
  backendOnline,
  sending,
  loadingHistory,
  sendMessage,
  chatMode,
  webSearchEnabled,
  cancelMessage,
  retryMessage,
  showCitations,
  sessionId,
} = useWorkspace()
const evidenceOpen = ref(false)
const evidenceModal = ref(false)
const evidenceTrigger = ref<HTMLButtonElement | null>(null)
const selectedCitationId = ref<string | null>(null)
const draft = ref('')
const composer = ref<HTMLTextAreaElement | null>(null)
const conversation = ref<HTMLElement | null>(null)
const nearBottom = ref(true)
const copiedMessage = ref<string | null>(null)
const copyError = ref('')
let copyTimer: ReturnType<typeof setTimeout> | undefined

function updateEvidenceViewport(): void {
  evidenceModal.value = window.innerWidth <= 900
}

onMounted(() => {
  updateEvidenceViewport()
  window.addEventListener('resize', updateEvidenceViewport)
  // Returning from the library mounts a new scroll container with existing messages.
  void nextTick(scrollToLatest)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', updateEvidenceViewport)
  clearTimeout(copyTimer)
})

const prompts = computed(() =>
  chatMode.value === 'knowledge'
    ? [
        { title: '弄清审批流程', text: '差旅报销需要经过哪些审批？', icon: Connection },
        { title: '找到制度依据', text: '合同归档需要保留哪些材料？', icon: Document },
        { title: '确认具体规则', text: '差旅报销超过 5000 元需要谁审批？', icon: Search },
      ]
    : [
        { title: '整理工作思路', text: '帮我整理一份产品发布会清单。', icon: Collection },
        { title: '把概念讲清楚', text: '用一个简单例子解释什么是项目里程碑。', icon: Connection },
        { title: '写得更清晰', text: '帮我拟一封邀请同事参加项目复盘的邮件。', icon: Document },
      ],
)
const canSend = computed(() => draft.value.trim().length > 0 && !sending.value)
const modeLocked = computed(() => messages.value.length > 0 || loadingHistory.value)
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
    await nextTick()
    if (nearBottom.value) scrollToLatest()
  },
)

watch(sessionId, () => {
  nearBottom.value = true
  evidenceOpen.value = false
  selectedCitationId.value = null
  draft.value = ''
})

function trackScroll(): void {
  const element = conversation.value
  if (element)
    nearBottom.value = element.scrollHeight - element.scrollTop - element.clientHeight < 80
}

function scrollToLatest(): void {
  const element = conversation.value
  if (element) element.scrollTop = messages.value.length ? element.scrollHeight : 0
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

async function submit() {
  if (!canSend.value) return
  const content = draft.value
  draft.value = ''
  nearBottom.value = true
  await sendMessage(content)
}

function openCitations(citations: Citation[], citationId?: string) {
  const normalizedCitationId = citationId?.trim().toUpperCase()
  const targetCitation =
    citations.find((citation) => citation.citation_id.trim().toUpperCase() === normalizedCitationId)
      ?.citation_id ??
    citations[0]?.citation_id ??
    null
  showCitations(citations)
  selectedCitationId.value = targetCitation
  evidenceOpen.value = true
}

function closeEvidence() {
  evidenceOpen.value = false
  selectedCitationId.value = null
  nextTick(() => evidenceTrigger.value?.focus())
}

function handleMessageContentClick(event: MouseEvent, citations: Citation[]) {
  const contentElement = event.currentTarget as HTMLElement
  const target = (event.target as HTMLElement).closest<HTMLButtonElement>('[data-citation-id]')
  if (!target || !contentElement.contains(target)) return

  const citationId = target.dataset.citationId
  if (
    !citationId ||
    !citations.some(
      (citation) => citation.citation_id.trim().toUpperCase() === citationId.trim().toUpperCase(),
    )
  ) {
    return
  }
  openCitations(citations, citationId)
}

function selectMode(mode: 'knowledge' | 'general') {
  if (sending.value || modeLocked.value) return
  chatMode.value = mode
}
</script>

<template>
  <div class="chat-layout" :class="{ 'evidence-visible': evidenceOpen }">
    <section
      class="chat-surface"
      :aria-hidden="evidenceOpen && evidenceModal ? 'true' : undefined"
      :inert="evidenceOpen && evidenceModal ? true : undefined"
    >
      <header class="workspace-header">
        <div>
          <h1>{{ activeSessionTitle }}</h1>
          <span class="workspace-context">{{
            chatMode === 'knowledge' ? '企业知识问答' : '通用助手'
          }}</span>
        </div>
        <button
          ref="evidenceTrigger"
          class="secondary-button"
          type="button"
          aria-label="打开或隐藏引用证据"
          @click="evidenceOpen ? closeEvidence() : openCitations(activeCitations)"
        >
          <el-icon aria-hidden="true"><Document /></el-icon>
          {{ evidenceOpen ? '隐藏证据' : '查看证据' }}
          <span v-if="activeCitations.length"> · {{ activeCitations.length }}</span>
        </button>
      </header>

      <div
        ref="conversation"
        class="conversation"
        aria-label="对话内容"
        tabindex="0"
        @scroll="trackScroll"
      >
        <div v-if="loadingHistory" class="view-loading">正在加载会话记录…</div>
        <div v-else-if="messages.length === 0" class="chat-empty">
          <div class="welcome-mark" aria-hidden="true">
            <el-icon><Collection /></el-icon>
          </div>
          <span class="welcome-eyebrow">{{
            chatMode === 'knowledge' ? '让团队知识，成为你的答案' : '你的日常工作伙伴'
          }}</span>
          <h2>
            {{
              chatMode === 'knowledge'
                ? '工作中的问题，在这里找到答案。'
                : '从一个问题，打开更多思路。'
            }}
          </h2>
          <p>
            {{
              chatMode === 'knowledge'
                ? '查制度、理流程、找依据。连接企业文档，让每一次回答都有迹可循。'
                : '一起整理思路、起草内容，也可以开启联网搜索查找最新信息。'
            }}
          </p>
          <div v-if="!backendOnline" class="offline-banner">
            暂时无法连接知识服务，请检查网络或联系管理员后重试。
          </div>
          <div class="prompt-grid">
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
              <el-icon class="prompt-action" aria-hidden="true"><Right /></el-icon>
            </button>
          </div>
        </div>

        <div v-else class="message-list">
          <article
            v-for="message in messages"
            :key="message.id"
            class="message"
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
                  (message.citations ?? []).map((citation) => citation.citation_id),
                )
              "
              @click="handleMessageContentClick($event, message.citations ?? [])"
            />
            <div v-else-if="message.answerState === 'validating'" class="answer-placeholder">
              正在校验回答依据…
            </div>
            <div v-else-if="message.answerState === 'streaming'" class="answer-placeholder">
              正在整理经过校验的最终回答…
            </div>
            <div v-else-if="message.answerState === 'cancelled'" class="answer-placeholder">
              本次回答已取消。
            </div>
            <div v-if="message.citations?.length" class="citation-row">
              <button
                v-for="citation in message.citations"
                :key="citation.citation_id"
                type="button"
                @click="openCitations(message.citations ?? [], citation.citation_id)"
              >
                {{ citation.citation_id }} · {{ citation.title }}
              </button>
            </div>
            <FailureNotice
              v-if="message.answerState === 'fallback' || message.answerState === 'error'"
              :answer-state="message.answerState"
              :failure-type="message.failureType"
              :failure-stage="message.failureStage"
              :trace-id="message.traceId"
              :retryable="
                message.answerState === 'error' || ['generation_error', 'citation_error', 'search_error', 'search_answer_error'].includes(message.failureType || '')
              "
              @retry="retryMessage(message)"
            />
            <div
              v-if="message.role === 'assistant' && message.content && message.state === 'complete'"
              class="answer-actions"
            >
              <button
                type="button"
                class="text-button"
                @click="copyAnswer(message.id, message.content)"
              >
                <el-icon aria-hidden="true"
                  ><Check v-if="copiedMessage === message.id" /><CopyDocument v-else
                /></el-icon>
                {{ copiedMessage === message.id ? '已复制回答' : '复制回答' }}
              </button>
              <span v-if="message.citations?.length"
                >{{ message.citations.length }} 条引用 · 点击编号查看来源</span
              >
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
        <AgentProgress :steps="agentSteps" :active="sending" />
        <form class="composer" @submit.prevent="submit">
          <div v-if="!modeLocked" class="mode-switcher" role="group" aria-label="对话模式">
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
              <el-icon aria-hidden="true"><component :is="mode.icon" /></el-icon>
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
            :disabled="sending"
            @keydown.ctrl.enter.prevent="submit"
            @keydown.meta.enter.prevent="submit"
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
                ><el-icon aria-hidden="true"><Collection /></el-icon> 企业知识库</span
              >
              <span class="composer-shortcut">Ctrl / ⌘ + Enter 发送</span>
            </div>
            <button v-if="sending" class="secondary-button" type="button" @click="cancelMessage">
              取消请求
            </button>
            <button v-else class="primary-button" type="submit" :disabled="!canSend">
              <el-icon aria-hidden="true"><ArrowUp /></el-icon> 发送
            </button>
          </div>
        </form>
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
      :citations="activeCitations"
      :selected-citation-id="selectedCitationId"
      @close="closeEvidence"
    />
  </div>
</template>
