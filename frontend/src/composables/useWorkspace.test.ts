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
  serviceMocks.deleteSession.mockReset()
  serviceMocks.fetchHistory.mockReset()
  serviceMocks.fetchSessions.mockClear()
  const workspace = useWorkspace()
  workspace.sessions.value = []
  workspace.sessionsError.value = null
  workspace.historyError.value = null
  workspace.historyErrorSessionId.value = null
  workspace.toasts.value = []
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

  it('opens the next session after deleting the active session', async () => {
    const workspace = useWorkspace()
    workspace.sessions.value = [
      { session_id: 'session-1', title: '第一个', created_at: '', updated_at: '', last_message: '' },
      { session_id: 'session-2', title: '当前会话', created_at: '', updated_at: '', last_message: '' },
      { session_id: 'session-3', title: '下一个', created_at: '', updated_at: '', last_message: '' },
    ]
    workspace.sessionId.value = 'session-2'
    serviceMocks.deleteSession.mockResolvedValue({ message: 'ok' })
    serviceMocks.fetchHistory.mockResolvedValue([
      { role: 'user', content: '下一个问题', message_id: 'message-3' },
    ])

    await workspace.removeSession('session-2')

    expect(workspace.sessionId.value).toBe('session-3')
    expect(workspace.sessions.value.map((session) => session.session_id))
      .toEqual(['session-1', 'session-3'])
    expect(workspace.messages.value[0]?.content).toBe('下一个问题')
  })

  it('records history load failures and retries the same session', async () => {
    const workspace = useWorkspace()
    workspace.sessionId.value = 'current'
    serviceMocks.fetchHistory
      .mockRejectedValueOnce(new Error('history unavailable'))
      .mockResolvedValueOnce([
        { role: 'assistant', content: '已恢复', message_id: 'message-recovered' },
      ])

    expect(await workspace.openSession('session-1')).toBe(false)
    expect(workspace.historyError.value).toContain('暂时无法加载')
    expect(workspace.historyErrorSessionId.value).toBe('session-1')

    expect(await workspace.retryOpenSession()).toBe(true)
    expect(workspace.historyError.value).toBeNull()
    expect(workspace.sessionId.value).toBe('session-1')
    expect(workspace.messages.value[0]?.content).toBe('已恢复')
  })

  it('keeps a retryable error when the adjacent session cannot be loaded after deletion', async () => {
    const workspace = useWorkspace()
    workspace.sessions.value = [
      { session_id: 'session-1', title: '当前会话', created_at: '', updated_at: '', last_message: '' },
      { session_id: 'session-2', title: '邻近会话', created_at: '', updated_at: '', last_message: '' },
    ]
    workspace.sessionId.value = 'session-1'
    serviceMocks.deleteSession.mockResolvedValue({ message: 'ok' })
    serviceMocks.fetchHistory.mockRejectedValue(new Error('history unavailable'))

    await workspace.removeSession('session-1')

    expect(workspace.messages.value).toEqual([])
    expect(workspace.historyError.value).toContain('暂时无法加载')
    expect(workspace.historyErrorSessionId.value).toBe('session-2')
    expect(workspace.sessions.value.map((session) => session.session_id)).toEqual(['session-2'])
    expect(workspace.toasts.value.at(-1)?.title).toBe('会话已删除')
    expect(workspace.toasts.value.at(-1)?.detail).toBe('聊天记录与 Agent 状态已同步清除。')
  })

  it('hides internal deletion errors from user-facing toasts', async () => {
    const workspace = useWorkspace()
    workspace.sessions.value = [
      { session_id: 'session-1', title: '当前会话', created_at: '', updated_at: '', last_message: '' },
    ]
    workspace.sessionId.value = 'session-1'
    serviceMocks.deleteSession.mockRejectedValue(new Error('Checkpointer is not initialized.'))

    await expect(workspace.removeSession('session-1')).rejects.toThrow('Checkpointer is not initialized.')

    expect(workspace.toasts.value.at(-1)?.detail).toBe('会话操作未完成，请稍后重试。')
  })
})
