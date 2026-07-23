<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'

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
  cancelMessage,
  showCitations,
} = useWorkspace()
const evidenceOpen = ref(window.matchMedia('(min-width: 901px)').matches)
const draft = ref('')
const composer = ref<HTMLTextAreaElement | null>(null)
const conversation = ref<HTMLElement | null>(null)

const prompts = [
  '报销流程需要经过哪些审批？',
  '合同归档需要保留哪些材料？',
  '查询制度中的具体金额与日期',
]
const canSend = computed(() => draft.value.trim().length > 0 && !sending.value)

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
</script>

<template>
  <div class="chat-layout" :class="{ 'evidence-visible': evidenceOpen }">
    <section class="chat-surface">
      <header class="workspace-header">
        <div>
          <p class="eyebrow">KNOWLEDGE CONVERSATION</p>
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
          <h2>让制度回答，<br />有据可查。</h2>
          <p>
            查询内部制度、流程、审批规则与合同规范。每一条关键结论，都可以回到原始文档。
          </p>
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
        <AgentProgress :steps="agentSteps" :active="sending" />
        <form class="composer" @submit.prevent="submit">
          <label for="question">向企业知识库提问</label>
          <textarea
            id="question"
            ref="composer"
            v-model="draft"
            rows="2"
            maxlength="12000"
            placeholder="例如：差旅报销超过 5000 元需要谁审批？"
            :disabled="sending"
            @keydown.ctrl.enter.prevent="submit"
            @keydown.meta.enter.prevent="submit"
          />
          <div class="composer-footer">
            <span>{{ draft.length }} 字 · Ctrl / ⌘ + Enter 发送</span>
            <button v-if="sending" class="secondary-button" type="button" @click="cancelMessage">
              取消请求
            </button>
            <button v-else class="primary-button" type="submit" :disabled="!canSend">
              发送问题
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
