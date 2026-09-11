<script setup lang="ts">
import { computed, ref } from 'vue'

import type { AnswerState } from '../types/api'

const props = defineProps<{
  answerState: AnswerState
  failureType?: string | null
  failureStage?: string | null
  traceId?: string | null
  retryable?: boolean
}>()

const emit = defineEmits<{ retry: [] }>()

const technicalOpen = ref(false)

interface FailureCopy {
  eyebrow: string
  title: string
  message: string
  guidance: string
}

const failureCopies: Record<string, FailureCopy> = {
  retrieval_miss: {
    eyebrow: 'KNOWLEDGE LIMIT',
    title: '没有找到足够相关的知识',
    message: '当前资料不足以支持可靠回答。',
    guidance: '请补充制度名称、部门或关键词后重新提问。',
  },
  citation_error: {
    eyebrow: 'EVIDENCE CHECK',
    title: '回答引用校验未通过',
    message: '系统没有提交未经验证的答案。',
    guidance: '请调整问题，或补充可引用的知识库资料。',
  },
  generation_error: {
    eyebrow: 'SERVICE LIMIT',
    title: '回答生成服务暂时不可用',
    message: '本次回答没有通过完整的生成流程。',
    guidance: '可以点击“重新发送”再次尝试。',
  },
  request_error: {
    eyebrow: 'REQUEST INCOMPLETE',
    title: '本次回答未完成',
    message: '未收到经过校验的最终回答，候选内容已丢弃。',
    guidance: '可以点击“重新发送”再次尝试。',
  },
}

const copy = computed<FailureCopy>(() => {
  if (props.answerState === 'error') return failureCopies.request_error
  return failureCopies[props.failureType || ''] || {
    eyebrow: 'ANSWER LIMITED',
    title: '回答受限',
    message: '系统没有提交未经验证的内容。',
    guidance: '请补充问题背景或知识库资料后再试。',
  }
})

const canRetry = computed(() => (
  props.retryable ?? (
    props.answerState === 'error' || props.failureType === 'generation_error'
  )
))

const stageLabels: Record<string, string> = {
  retrieval: '知识检索',
  relevance: '证据相关性评估',
  evidence: '证据准备',
  citation: '引用校验',
  hallucination: '回答一致性校验',
  generation: '回答生成',
  tool: '工具调用',
  runtime: '运行流程',
}

const technicalStage = computed(() => (
  stageLabels[props.failureStage || ''] || '处理流程'
))

function toggleTechnical(event: MouseEvent): void {
  event.preventDefault()
  technicalOpen.value = !technicalOpen.value
}
</script>

<template>
  <section
    class="failure-notice"
    :class="`failure-${answerState}`"
    role="status"
    aria-live="polite"
  >
    <span class="failure-icon" aria-hidden="true">!</span>
    <div class="failure-copy">
      <p class="eyebrow">{{ copy.eyebrow }}</p>
      <h3>{{ copy.title }}</h3>
      <p>{{ copy.message }}</p>
      <p class="failure-guidance">{{ copy.guidance }}</p>
      <div v-if="canRetry" class="failure-actions">
        <button
          class="failure-retry"
          type="button"
          @click="emit('retry')"
        >
          重新发送
        </button>
      </div>
      <details v-if="traceId || failureStage" class="technical-details" :open="technicalOpen">
        <summary @click="toggleTechnical">
          {{ technicalOpen ? '收起技术详情' : '查看技术详情' }}
        </summary>
        <div v-if="technicalOpen" class="technical-details-content">
          <span>处理阶段：{{ technicalStage }}</span>
          <code v-if="traceId">请求标识：{{ traceId }}</code>
        </div>
      </details>
    </div>
  </section>
</template>
