import type {
  AgentStep,
  AnswerState,
  Citation,
  StreamEvent,
} from '../types/api'

export interface StreamUiState {
  answerState: AnswerState
  content: string
  pendingContent: string
  resultReceived: boolean
  citations: Citation[]
  failureType: string | null
  failureStage: string | null
  failureReason: string | null
  traceId: string | null
  steps: AgentStep[]
}

const safeStatusMessages: Record<string, string> = {
  manage_conversation_context: '正在整理会话上下文',
  inject_memory: '正在读取用户偏好',
  route_query: '正在判断问题类型',
  retrieve: '正在检索知识库',
  grade_documents: '正在评估文档相关性',
  rewrite_query: '正在优化检索问题',
  tool_executor: '正在调用工具',
  generate: '正在组织回答',
  check_hallucination: '正在校验回答依据',
  commit_answer: '正在提交经过校验的回答',
  update_memory: '正在更新用户偏好',
  error: '回答处理未完成',
}

function now(): number {
  return Date.now()
}

function statusContent(node: string, content: string): string {
  void content
  return safeStatusMessages[node] || '正在处理请求'
}

function completeRunningSteps(steps: AgentStep[], endedAt: number): AgentStep[] {
  return steps.map((step) => (
    step.status === 'running'
      ? { ...step, status: 'complete', endedAt }
      : step
  ))
}

function reduceStatus(state: StreamUiState, event: Extract<StreamEvent, { type: 'status' }>): StreamUiState {
  const timestamp = now()
  const nextSteps = [...state.steps]
  const previous = nextSteps.at(-1)
  const explicitStatus = event.node_status

  if (event.node === 'error' || explicitStatus === 'error') {
    const failedSteps = nextSteps.map((step) => (
      step.status === 'running'
        ? { ...step, status: 'error' as const, endedAt: timestamp }
        : step
    ))
    if (previous?.node === event.node) {
      failedSteps[failedSteps.length - 1] = {
        ...previous,
        content: statusContent(event.node, event.content),
        status: 'error',
        endedAt: timestamp,
      }
    } else {
      failedSteps.push({
        node: event.node,
        content: statusContent(event.node, event.content),
        status: 'error',
        startedAt: timestamp,
        endedAt: timestamp,
      })
    }
    return {
      ...state,
      answerState: 'error',
      steps: failedSteps,
    }
  }

  if (previous?.node === event.node) {
    nextSteps[nextSteps.length - 1] = {
      ...previous,
      content: statusContent(event.node, event.content),
      status: explicitStatus || previous.status || 'running',
      endedAt: explicitStatus && explicitStatus !== 'running'
        ? timestamp
        : previous.endedAt,
    }
  } else {
    const completedSteps = completeRunningSteps(nextSteps, timestamp)
    completedSteps.push({
      node: event.node,
      content: statusContent(event.node, event.content),
      status: explicitStatus || 'running',
      startedAt: timestamp,
      endedAt: explicitStatus && explicitStatus !== 'running'
        ? timestamp
        : undefined,
    })
    nextSteps.splice(0, nextSteps.length, ...completedSteps)
  }

  return {
    ...state,
    answerState: event.node === 'check_hallucination' ? 'validating' : 'streaming',
    steps: nextSteps,
  }
}

export function createStreamState(): StreamUiState {
  return {
    answerState: 'streaming',
    content: '',
    pendingContent: '',
    resultReceived: false,
    citations: [],
    failureType: null,
    failureStage: null,
    failureReason: null,
    traceId: null,
    steps: [],
  }
}

export function reduceStreamEvent(state: StreamUiState, event: StreamEvent): StreamUiState {
  if (state.resultReceived && event.type !== 'done') {
    return state
  }
  if (state.answerState === 'error' && event.type !== 'result' && event.type !== 'done') {
    return state
  }

  if (event.type === 'status') {
    return reduceStatus(state, event)
  }

  if (event.type === 'token') {
    return state.resultReceived
      ? state
      : {
          ...state,
          answerState: 'streaming',
          pendingContent: state.pendingContent + event.content,
        }
  }

  if (event.type === 'result') {
    const timestamp = now()
    const failureType = event.failure_type || null
    return {
      ...state,
      answerState: failureType && failureType !== 'none' ? 'fallback' : 'complete',
      content: event.content,
      pendingContent: '',
      resultReceived: true,
      citations: event.citations ?? [],
      failureType,
      failureStage: event.failure_stage ?? null,
      failureReason: event.failure_reason ?? null,
      traceId: event.trace_id || state.traceId,
      steps: completeRunningSteps(state.steps, timestamp),
    }
  }

  return {
    ...state,
    traceId: event.trace_id || state.traceId,
  }
}
