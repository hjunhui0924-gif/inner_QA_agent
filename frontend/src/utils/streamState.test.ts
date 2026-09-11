import { describe, expect, it } from 'vitest'

import {
  createStreamState,
  reduceStreamEvent,
  type StreamUiState,
} from './streamState'

describe('stream state reducer', () => {
  it('completes the previous agent step when a new node starts', () => {
    let state = createStreamState()

    state = reduceStreamEvent(state, {
      type: 'status',
      node: 'retrieve',
      content: '正在检索知识库',
    })
    state = reduceStreamEvent(state, {
      type: 'status',
      node: 'generate',
      content: '正在生成回答',
    })

    expect(state.steps.map((step) => [step.node, step.status])).toEqual([
      ['retrieve', 'complete'],
      ['generate', 'running'],
    ])
  })

  it('uses a safe generic label for an unknown backend node', () => {
    const state = reduceStreamEvent(createStreamState(), {
      type: 'status',
      node: 'internal_secret_node',
      content: 'internal stack detail',
    })

    expect(state.steps[0]?.content).toBe('正在处理请求')
    expect(state.steps[0]?.content).not.toContain('internal stack detail')
  })

  it('marks an explicitly failed node as failed', () => {
    const state = reduceStreamEvent(createStreamState(), {
      type: 'status',
      node: 'retrieve',
      content: 'internal error detail',
      node_status: 'error',
    })

    expect(state.answerState).toBe('error')
    expect(state.steps[0]?.status).toBe('error')
    expect(state.steps[0]?.content).toBe('正在检索知识库')
  })

  it('keeps tokens provisional until the authoritative result arrives', () => {
    let state: StreamUiState = createStreamState()

    state = reduceStreamEvent(state, { type: 'token', content: '候选' })
    state = reduceStreamEvent(state, { type: 'token', content: '答案' })

    expect(state.pendingContent).toBe('候选答案')
    expect(state.content).toBe('')
    expect(state.resultReceived).toBe(false)

    state = reduceStreamEvent(state, {
      type: 'result',
      content: '最终答案',
      citations: [],
      trace_id: 'trace-1',
      failure_type: 'none',
    })

    expect(state.pendingContent).toBe('')
    expect(state.content).toBe('最终答案')
    expect(state.resultReceived).toBe(true)
    expect(state.answerState).toBe('complete')
  })

  it('represents an authoritative non-none result as a safe fallback', () => {
    const state = reduceStreamEvent(createStreamState(), {
      type: 'result',
      content: '资料不足，暂时无法回答。',
      citations: [],
      trace_id: 'trace-2',
      failure_type: 'retrieval_miss',
      failure_stage: 'evidence',
      failure_reason: '没有可供回答的检索证据。',
    })

    expect(state.answerState).toBe('fallback')
    expect(state.resultReceived).toBe(true)
    expect(state.content).toBe('资料不足，暂时无法回答。')
    expect(state.failureStage).toBe('evidence')
  })

  it('does not turn an already committed result back into provisional text', () => {
    let state = reduceStreamEvent(createStreamState(), {
      type: 'result',
      content: '最终答案',
      citations: [],
      trace_id: 'trace-3',
      failure_type: 'none',
    })

    state = reduceStreamEvent(state, { type: 'token', content: '不应追加' })

    expect(state.content).toBe('最终答案')
    expect(state.pendingContent).toBe('')
  })

  it('ignores late status and token events after a result is committed', () => {
    let state = reduceStreamEvent(createStreamState(), {
      type: 'result',
      content: '最终答案',
      citations: [],
      trace_id: 'trace-4',
      failure_type: 'none',
    })

    state = reduceStreamEvent(state, {
      type: 'status',
      node: 'error',
      content: 'late error detail',
    })
    state = reduceStreamEvent(state, { type: 'token', content: 'late token' })

    expect(state.answerState).toBe('complete')
    expect(state.content).toBe('最终答案')
    expect(state.steps).toEqual([])
  })

  it('accepts an authoritative result even when its content is empty', () => {
    const state = reduceStreamEvent(createStreamState(), {
      type: 'result',
      content: '',
      citations: [],
      trace_id: 'trace-5',
      failure_type: 'none',
    })

    expect(state.resultReceived).toBe(true)
    expect(state.answerState).toBe('complete')
    expect(state.content).toBe('')
  })

  it('keeps an error terminal when a late token arrives', () => {
    let state = reduceStreamEvent(createStreamState(), {
      type: 'status',
      node: 'error',
      content: 'runtime failure',
    })

    state = reduceStreamEvent(state, { type: 'token', content: 'late token' })

    expect(state.answerState).toBe('error')
    expect(state.pendingContent).toBe('')
  })
})
