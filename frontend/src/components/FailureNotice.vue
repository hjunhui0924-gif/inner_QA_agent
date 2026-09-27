<script setup lang="ts">
import { computed, ref } from 'vue'

import type { AnswerState } from '../types/api'

const props = defineProps<{
  answerState: AnswerState
  failureType?: string | null
  failureStage?: string | null
  traceId?: string | null
  retryable?: boolean
  busy?: boolean
}>()

const emit = defineEmits<{ retry: []; edit: []; withoutWeb: [] }>()

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
    guidance: '已找到相关资料，但暂未生成可验证的回答，可以重新尝试。',
  },
  search_no_results: {
    eyebrow: 'WEB SEARCH', title: '没有找到可用的网页来源',
    message: '本次搜索没有获得可用于回答的信息。', guidance: '请调整关键词，或补充具体名称和时间范围。',
  },
  search_error: {
    eyebrow: 'WEB SEARCH', title: '联网搜索暂时不可用',
    message: '当前无法获取网页来源。', guidance: '可以重试，或修改问题后再试。',
  },
  search_answer_error: {
    eyebrow: 'WEB SEARCH', title: '联网回答校验未通过',
    message: '已获得网页来源，但暂未生成引用完整的回答。', guidance: '可以重试，或修改问题后再试。',
  },
  generation_error: {
    eyebrow: 'SERVICE LIMIT',
    title: '回答生成服务暂时不可用',
    message: '本次回答没有通过完整的生成流程。',
    guidance: '可以重试，或修改问题后再试。',
  },
  abstention_error: {
    eyebrow: 'KNOWLEDGE LIMIT',
    title: '当前资料不足以回答这个问题',
    message: '系统没有提交未经验证的答案。',
    guidance: '请补充制度名称、部门或关键词后重新提问。',
  },
  request_error: {
    eyebrow: 'REQUEST INCOMPLETE',
    title: '本次回答未完成',
    message: '本次未收到完整的最终回答。',
    guidance: '可以重试，或修改问题后再试。',
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
    props.answerState === 'error' || ['generation_error', 'citation_error', 'search_error', 'search_answer_error'].includes(props.failureType || '')
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
  >
    <span class="sr-only" role="status" aria-live="polite">
      {{ copy.title }}。{{ copy.message }}
    </span>
    <span class="failure-icon" aria-hidden="true">!</span>
    <div class="failure-copy">
      <p class="eyebrow">{{ copy.eyebrow }}</p>
      <h3>{{ copy.title }}</h3>
      <p>{{ copy.message }}</p>
      <p class="failure-guidance">{{ copy.guidance }}</p>
      <div class="failure-actions">
        <button
          v-if="canRetry"
          class="failure-retry"
          type="button"
          :disabled="busy"
          @click="emit('retry')"
        >
          重新发送
        </button>
        <button type="button" class="text-button" :disabled="busy" @click="emit('edit')">编辑问题</button>
        <button v-if="failureType?.startsWith('search_')" type="button" class="text-button" :disabled="busy" @click="emit('withoutWeb')">关闭联网，使用通用知识回答</button>
      </div>
      <p v-if="failureType?.startsWith('search_')" class="failure-guidance">不联网回答无法保证信息为最新。</p>
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
