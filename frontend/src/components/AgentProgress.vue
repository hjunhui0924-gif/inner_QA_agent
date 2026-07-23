<script setup lang="ts">
import type { AgentStep } from '../types/api'

defineProps<{ steps: AgentStep[]; active: boolean }>()

const labels: Record<string, string> = {
  manage_conversation_context: '整理上下文',
  route_query: '识别问题',
  retrieve: '检索知识',
  grade_documents: '评估证据',
  rewrite_query: '优化查询',
  tool_executor: '调用工具',
  generate: '组织回答',
  check_hallucination: '校验回答',
  error: '运行异常',
}
</script>

<template>
  <div v-if="active || steps.length" class="agent-progress" aria-live="polite">
    <div class="progress-heading">
      <span class="status-dot online" :class="{ pulse: active }" />
      <strong>{{ active ? 'Agent 正在处理' : 'Agent 路径' }}</strong>
    </div>
    <div class="step-list">
      <span v-for="(step, index) in steps" :key="`${step.node}-${index}`" class="step-chip">
        {{ labels[step.node] || step.content }}
      </span>
    </div>
  </div>
</template>
