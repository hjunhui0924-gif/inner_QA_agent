<script setup lang="ts">
import { computed } from 'vue'
import { Check, Collection, CopyDocument } from '@element-plus/icons-vue'

import AnswerPreview from './AnswerPreview.vue'
import AgentProgress from './AgentProgress.vue'
import FailureNotice from './FailureNotice.vue'
import type { AgentStep, UiMessage } from '../types/api'
import type { ActionState } from '../types/ui'
import { renderMarkdown } from '../utils/markdown'
import { recordMarkdownRender } from '../utils/performance'

export interface AttemptSummary {
  id: string
  attempts: UiMessage[][]
}

const props = defineProps<{
  message: UiMessage
  attemptSummary?: AttemptSummary
  expanded: boolean
  sending: boolean
  active: boolean
  agentSteps: AgentStep[]
  sendingStartedAt: number
  copyState: ActionState
  retryState: ActionState
}>()

const emit = defineEmits<{
  toggleAttempts: [id: string]
  contentClick: [event: MouseEvent, message: UiMessage]
  citation: [message: UiMessage, event: Event, citationId: string]
  cancel: []
  retry: [message: UiMessage]
  edit: [message: UiMessage]
  withoutWeb: [message: UiMessage]
  copy: [message: UiMessage]
  feedback: [message: UiMessage, event: Event]
}>()

type RenderedMarkdown = { content: string; citationKey: string; html: string }
let contentCache: RenderedMarkdown | null = null

function renderCached(content: string, citationIds: string[]): string {
  const citationKey = citationIds.join('\u001f')
  const previous = contentCache
  if (previous?.content === content && previous.citationKey === citationKey)
    return previous.html
  const started = performance.now()
  const html = renderMarkdown(content, citationIds)
  recordMarkdownRender(performance.now() - started)
  const rendered = { content, citationKey, html }
  contentCache = rendered
  return html
}

const contentHtml = computed(() => {
  const message = props.message
  if (!message.content || message.answerState === 'fallback' || message.answerState === 'error') return ''
  return renderCached(
    message.content,
    (message.citations ?? []).map((citation) => citation.citation_id),
  )
})

const processSteps = computed(() =>
  (props.message.processSummary?.steps ?? []).map((step) => ({
    node: step.node,
    content: '',
    status: step.status,
    durationMs: step.duration_ms,
  })),
)

const canRetry = computed(() =>
  props.message.answerState === 'error' ||
  [
    'generation_error',
    'citation_error',
    'search_error',
    'search_answer_error',
  ].includes(props.message.failureType || ''),
)

const previewLabel = computed(() =>
  ['cancelled', 'error'].includes(props.message.answerState || '')
    ? '未完成草稿 · 本次回答未完成'
    : '生成中 · 尚未完成',
)
</script>

<template>
  <article
    class="message"
    :data-message-id="message.message_id || message.id"
    tabindex="-1"
    :class="[message.role, message.state]"
  >
    <button
      v-if="attemptSummary"
      type="button"
      class="text-button attempt-toggle"
      :aria-expanded="expanded"
      @click="emit('toggleAttempts', attemptSummary.id)"
    >
      {{ expanded ? '收起' : '查看' }}此前 {{ attemptSummary.attempts.length - 1 }} 次尝试
    </button>
    <span class="message-label">
      <el-icon v-if="message.role === 'assistant'" aria-hidden="true"><Collection /></el-icon>
      {{ message.role === 'user' ? '你' : '知识助手' }}
    </span>
    <div
      v-if="contentHtml"
      class="message-content"
      v-html="contentHtml"
      @click="emit('contentClick', $event, message)"
    />
    <AnswerPreview
      v-if="message.previewContent && message.answerState !== 'complete' && message.answerState !== 'fallback'"
      :content="message.previewContent"
      :label="previewLabel"
    />
    <div v-if="message.role === 'assistant' && sending && active" class="answer-wait">
      <AgentProgress :steps="agentSteps" :active="true" :started-at="sendingStartedAt" />
      <button type="button" class="text-button" @click="emit('cancel')">取消请求</button>
    </div>
    <AgentProgress
      v-else-if="message.role === 'assistant' && message.processSummary?.steps?.length"
      :steps="processSteps"
      :active="false"
      :final-status="message.processSummary.status"
      :elapsed-ms="message.processSummary.elapsed_ms"
    />
    <div v-if="message.answerState === 'cancelled'" class="answer-placeholder">
      本次回答已取消。
      <div class="answer-actions">
        <button type="button" class="secondary-button" :disabled="sending || retryState === 'loading'" @click="emit('retry', message)">
          {{ retryState === 'loading' ? '重新生成中…' : retryState === 'success' ? '已重新生成' : retryState === 'error' ? '重新生成失败，重试' : '重新生成' }}
        </button>
        <button type="button" class="text-button" :disabled="sending" @click="emit('edit', message)">编辑问题</button>
      </div>
    </div>
    <div v-if="message.citations?.length" class="citation-row">
      <p class="reference-heading">参考来源</p>
      <ol class="reference-list">
        <li v-for="(citation, index) in message.citations" :key="citation.citation_id">
          <button type="button" @click="emit('citation', message, $event, citation.citation_id)">
            <span class="reference-number">[{{ index + 1 }}]</span>
            <span>{{ citation.title || '未命名文档' }}<span v-if="citation.page"> · 第 {{ citation.page }} 页</span></span>
          </button>
        </li>
      </ol>
    </div>
    <FailureNotice
      v-if="message.answerState === 'fallback' || message.answerState === 'error'"
      :answer-state="message.answerState"
      :busy="sending || retryState === 'loading'"
      :failure-type="message.failureType"
      :failure-stage="message.failureStage"
      :trace-id="message.traceId"
      :retryable="canRetry"
      @retry="emit('retry', message)"
      @edit="emit('edit', message)"
      @without-web="emit('withoutWeb', message)"
    />
    <div
      v-if="message.role === 'assistant' && message.content && message.answerState === 'complete'"
      class="answer-actions"
    >
      <button
        type="button"
        class="text-button"
        :disabled="copyState === 'loading'"
        :aria-live="copyState === 'success' || copyState === 'error' ? 'polite' : undefined"
        @click="emit('copy', message)"
      >
        <el-icon aria-hidden="true"><Check v-if="copyState === 'success'" /><CopyDocument v-else /></el-icon>
        {{ copyState === 'loading' ? '复制中…' : copyState === 'success' ? '已复制回答' : copyState === 'error' ? '复制失败，重试' : '复制回答' }}
      </button>
      <button type="button" class="text-button" @click="emit('feedback', message, $event)">反馈回答</button>
    </div>
  </article>
</template>
