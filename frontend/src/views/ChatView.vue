<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { Connection, Collection, ArrowUp, Search } from '@element-plus/icons-vue'

import AgentProgress from '../components/AgentProgress.vue'
import EvidencePanel from '../components/EvidencePanel.vue'
import { useWorkspace } from '../composables/useWorkspace'
import type { Citation } from '../types/api'
import { renderMarkdown } from '../utils/markdown'

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
  showCitations,
} = useWorkspace()
const evidenceOpen = ref(false)
const draft = ref('')
const composer = ref<HTMLTextAreaElement | null>(null)
const conversation = ref<HTMLElement | null>(null)

const prompts = [
  '报销流程需要经过哪些审批？',
  '合同归档需要保留哪些材料？',
  '查询制度中的具体金额与日期',
]
const canSend = computed(() => draft.value.trim().length > 0 && !sending.value)
const modeLocked = computed(() => messages.value.length > 0 || loadingHistory.value)
const modes = [
  { id: 'knowledge' as const, label: '知识库', description: '只基于企业知识库回答', icon: Collection },
  { id: 'general' as const, label: '通用模式', description: '开放问答，可连接互联网', icon: Connection },
]

watch(
  () => [messages.value.length, messages.value.at(-1)?.content],
  async () => {
    await nextTick()
    if (conversation.value) conversation.value.scrollTop = conversation.value.scrollHeight
  },
)

async function usePrompt(prompt: string) {
  draft.value = prompt
  await nextTick()
  composer.value?.focus()
}

async function submit() {
  if (!canSend.value) return
  const content = draft.value
  draft.value = ''
  await sendMessage(content)
}

function openCitations(citations: Citation[]) {
  showCitations(citations)
  evidenceOpen.value = true
}

function selectMode(mode: 'knowledge' | 'general') {
  if (sending.value || modeLocked.value) return
  chatMode.value = mode
}
</script>

<template>
  <div class="chat-layout" :class="{ 'evidence-visible': evidenceOpen }">
    <section class="chat-surface">
      <header class="workspace-header">
        <div>
          <div class="brand-heading"><img src="/knowledge-assistant.png" alt="" /><p class="eyebrow">KNOWLEDGE ASSISTANT</p></div>
          <h1>{{ activeSessionTitle }}</h1>
        </div>
        <button class="secondary-button" type="button" @click="evidenceOpen = !evidenceOpen">
          {{ evidenceOpen ? '隐藏证据' : '查看证据' }}
          <span v-if="activeCitations.length"> · {{ activeCitations.length }}</span>
        </button>
      </header>

      <div ref="conversation" class="conversation" aria-live="polite">
        <div v-if="loadingHistory" class="view-loading">正在加载会话记录…</div>
        <div v-else-if="messages.length === 0" class="chat-empty">
          <span class="chapter-mark">01 / ASK</span>
          <h2>{{ chatMode === 'knowledge' ? '让制度回答，有据可查。' : '想知道什么，直接问我。' }}</h2>
          <p>{{ chatMode === 'knowledge' ? '查询内部制度、流程、审批规则与合同规范。每一条关键结论，都可以回到原始文档。' : '处理开放性问题，通用模式可在需要时连接互联网获取最新信息。' }}</p>
          <div v-if="!backendOnline" class="offline-banner">
            知识服务当前离线。启动 FastAPI 后即可开始问答。
          </div>
          <div class="prompt-grid">
            <button
              v-for="prompt in prompts"
              :key="prompt"
              type="button"
              :disabled="sending"
              @click="usePrompt(prompt)"
            >
              <span>{{ prompt }}</span>
              <span class="prompt-action">填入</span>
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
            <span class="message-label">{{ message.role === 'user' ? '你' : '知识助手' }}</span>
            <AgentProgress
              v-if="message.role === 'assistant' && message.state === 'streaming'"
              :steps="agentSteps"
              :active="sending"
            />
            <div
              v-if="message.content"
              class="message-content"
              v-html="renderMarkdown(message.content)"
            />
            <div v-else-if="message.state === 'streaming'" class="answer-placeholder">
              正在等待经过校验的最终回答…
            </div>
            <div v-if="message.citations?.length" class="citation-row">
              <button
                v-for="citation in message.citations"
                :key="citation.citation_id"
                type="button"
                @click="openCitations(message.citations ?? [])"
              >
                {{ citation.citation_id }} · {{ citation.title }}
              </button>
            </div>
            <span v-if="message.failureType && message.failureType !== 'none'" class="failure-label">
              {{ message.failureType }}
            </span>
          </article>
        </div>
      </div>

      <div class="composer-wrap">
        <div v-if="!modeLocked" class="mode-switcher" role="tablist" aria-label="对话模式">
          <span class="mode-slider" :class="{ general: chatMode === 'general' }" aria-hidden="true" />
          <button
            v-for="mode in modes"
            :key="mode.id"
            class="mode-option"
            :class="{ active: chatMode === mode.id }"
            type="button"
            role="tab"
            :aria-selected="chatMode === mode.id"
            :disabled="sending"
            @click="selectMode(mode.id)"
          >
            <el-icon><component :is="mode.icon" /></el-icon>
            <span>{{ mode.label }}</span>
            <small>{{ mode.description }}</small>
          </button>
        </div>
        <AgentProgress :steps="agentSteps" :active="sending" />
        <form class="composer" @submit.prevent="submit">
          <label for="question">{{ chatMode === 'knowledge' ? '向企业知识库提问' : '向通用助手提问' }}</label>
          <textarea
            id="question"
            ref="composer"
            v-model="draft"
            rows="2"
            maxlength="12000"
            :placeholder="chatMode === 'knowledge' ? '例如：差旅报销超过 5000 元需要谁审批？' : '例如：帮我整理一份产品发布会清单。'"
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
              ><el-icon><Search /></el-icon> 联网搜索</button>
              <span>{{ draft.length }} 字 · Ctrl / ⌘ + Enter 发送</span>
            </div>
            <button v-if="sending" class="secondary-button" type="button" @click="cancelMessage">
              取消请求
            </button>
            <button v-else class="primary-button" type="submit" :disabled="!canSend">
              <el-icon><ArrowUp /></el-icon> 发送
            </button>
          </div>
        </form>
      </div>
    </section>

    <EvidencePanel
      :open="evidenceOpen"
      :citations="activeCitations"
      @close="evidenceOpen = false"
    />
  </div>
</template>
