<script setup lang="ts">
import type { AgentStep } from '../types/api'

defineProps<{ steps: AgentStep[]; active: boolean }>()

const labels: Record<string, string> = {
  manage_conversation_context: '整理上下文',
  inject_memory: '读取偏好',
  route_query: '识别问题',
  retrieve: '检索知识',
  grade_documents: '评估证据',
  rewrite_query: '优化查询',
  tool_executor: '调用工具',
  generate: '组织回答',
  check_hallucination: '校验回答',
  error: '运行异常',
  update_memory: '更新偏好',
}

const descriptions: Record<string, string> = {
  manage_conversation_context: '整理当前会话中的必要上下文',
  inject_memory: '读取当前用户偏好信息',
  route_query: '判断问题应使用哪条处理路径',
  retrieve: '从企业知识库查找相关内容',
  grade_documents: '检查候选文档与问题的相关性',
  rewrite_query: '根据检索结果调整问题表达',
  tool_executor: '执行当前问题所需的外部工具',
  generate: '根据上下文组织回答草稿',
  check_hallucination: '核对回答与证据是否一致',
  commit_answer: '提交可以展示给用户的结果',
  error: '处理过程中出现异常',
  update_memory: '更新当前用户偏好信息',
}

const statusLabels: Record<string, string> = {
  pending: '待处理',
  running: '处理中',
  complete: '已完成',
  skipped: '已跳过',
  error: '失败',
}

function stepLabel(step: AgentStep): string {
  return labels[step.node] || '处理请求'
}

function stepDescription(step: AgentStep): string {
  return descriptions[step.node] || '正在处理请求'
}

function stepStatus(step: AgentStep): NonNullable<AgentStep['status']> {
  return step.status || 'pending'
}

function stepAriaLabel(step: AgentStep): string {
  const status = stepStatus(step)
  return `${stepLabel(step)}：${statusLabels[status] || '处理中'}`
}
</script>

<template>
  <div v-if="active || steps.length" class="agent-progress">
    <div class="progress-heading" role="status" aria-live="polite">
      <span class="status-dot online" :class="{ pulse: active }" />
      <strong>{{ active ? 'Agent 正在处理' : 'Agent 路径' }}</strong>
    </div>
    <div class="step-list" role="list" aria-label="Agent 处理步骤">
      <div
        v-for="(step, index) in steps"
        :key="`${step.node}-${index}`"
        class="step-chip"
        :class="`step-${stepStatus(step)}`"
        role="listitem"
        :aria-label="stepAriaLabel(step)"
      >
        <span class="step-status-marker" aria-hidden="true" />
        <span class="step-copy">
          <strong>{{ stepLabel(step) }}</strong>
          <small>{{ stepDescription(step) }}</small>
        </span>
      </div>
    </div>
  </div>
</template>
