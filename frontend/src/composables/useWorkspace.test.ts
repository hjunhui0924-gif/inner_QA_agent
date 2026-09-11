import { afterEach, describe, expect, it, vi } from 'vitest'

import type { StreamEvent } from '../types/api'

const serviceMocks = vi.hoisted(() => ({
  deleteSession: vi.fn(),
  fetchHealth: vi.fn(),
  fetchHistory: vi.fn(),
  fetchKnowledgeRecords: vi.fn(async () => []),
  fetchSessions: vi.fn(async () => []),
  streamChat: vi.fn(),
  uploadKnowledge: vi.fn(),
}))

vi.mock('../services/api', () => serviceMocks)

import { useWorkspace } from './useWorkspace'

function resultEvent(
  content: string,
  failureType = 'none',
): Extract<StreamEvent, { type: 'result' }> {
  return {
    type: 'result',
    content,
    citations: [],
    trace_id: 'trace-test',
    turn_id: 'server-turn',
    failure_type: failureType,
    failure_stage: failureType === 'none' ? null : 'generation',
    failure_reason: failureType === 'none' ? null : 'generation failed',
  }
}

afterEach(() => {
  serviceMocks.streamChat.mockReset()
  serviceMocks.fetchSessions.mockClear()
  const workspace = useWorkspace()
  workspace.newSession()
})

describe('useWorkspace answer delivery', () => {
  it('does not expose provisional tokens before committing a fallback result', async () => {
    serviceMocks.streamChat.mockImplementationOnce(async (_payload, onEvent) => {
      onEvent({ type: 'status', node: 'generate', content: '正在生成回答' })
      onEvent({ type: 'token', content: '未经确认的候选内容' })
      onEvent(resultEvent('回答服务暂时不可用。', 'generation_error'))
      onEvent({ type: 'done', trace_id: 'trace-1' })
    })
    const workspace = useWorkspace()

    await workspace.sendMessage('请回答这个问题')

    const assistant = workspace.messages.value.at(-1)
    expect(assistant?.content).toBe('回答服务暂时不可用。')
    expect(assistant?.answerState).toBe('fallback')
    expect(assistant?.state).toBe('complete')
    expect(assistant?.content).not.toContain('未经确认')
  })

  it('keeps an incomplete request empty and retries with a new turn ID', async () => {
    const payloads: Array<{ turn_id?: string }> = []
    serviceMocks.streamChat
      .mockImplementationOnce(async (payload) => {
        payloads.push(payload)
        throw new Error('network down')
      })
      .mockImplementationOnce(async (payload, onEvent) => {
        payloads.push(payload)
        onEvent(resultEvent('重试后的最终答案'))
        onEvent({ type: 'done', trace_id: 'trace-2' })
      })
    const workspace = useWorkspace()

    await workspace.sendMessage('请重试这个问题')
    const failedAssistant = workspace.messages.value.at(-1)
    expect(failedAssistant?.answerState).toBe('error')
    expect(failedAssistant?.content).toBe('')

    await workspace.retryMessage(failedAssistant!)

    expect(payloads).toHaveLength(2)
    expect(payloads[0]?.turn_id).toBeTruthy()
    expect(payloads[1]?.turn_id).toBeTruthy()
    expect(payloads[1]?.turn_id).not.toBe(payloads[0]?.turn_id)
    expect(workspace.messages.value.at(-1)?.content).toBe('重试后的最终答案')
  })

  it('keeps an empty authoritative result when refreshing sessions fails afterward', async () => {
    serviceMocks.streamChat.mockImplementationOnce(async (_payload, onEvent) => {
      onEvent(resultEvent(''))
      onEvent({ type: 'done', trace_id: 'trace-empty' })
    })
    serviceMocks.fetchSessions.mockRejectedValueOnce(new Error('session refresh failed'))
    const workspace = useWorkspace()

    await workspace.sendMessage('返回空结果的测试问题')

    const assistant = workspace.messages.value.at(-1)
    expect(assistant?.answerState).toBe('complete')
    expect(assistant?.state).toBe('complete')
    expect(assistant?.content).toBe('')
  })

  it('discards provisional tokens when the request is cancelled', async () => {
    serviceMocks.streamChat.mockImplementationOnce(async (_payload, onEvent, signal) => {
      onEvent({ type: 'status', node: 'retrieve', content: '正在检索知识库' })
      onEvent({ type: 'token', content: '不应显示的候选内容' })
      await new Promise<void>((_resolve, reject) => {
        signal?.addEventListener(
          'abort',
          () => reject(new DOMException('The request was aborted.', 'AbortError')),
          { once: true },
        )
      })
    })
    const workspace = useWorkspace()
    const sending = workspace.sendMessage('需要取消的问题')

    await vi.waitFor(() => expect(workspace.sending.value).toBe(true))
    workspace.cancelMessage()
    await sending

    const assistant = workspace.messages.value.at(-1)
    expect(assistant?.answerState).toBe('cancelled')
    expect(assistant?.state).toBe('cancelled')
    expect(assistant?.content).toBe('')
    expect(workspace.agentSteps.value).toEqual([])
  })
})
