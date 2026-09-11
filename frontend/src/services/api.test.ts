import { afterEach, describe, expect, it, vi } from 'vitest'

import { streamChat, uploadKnowledge } from './api'
import type { StreamEvent } from '../types/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('streamChat', () => {
  it('parses SSE events split across arbitrary network chunks', async () => {
    const encoder = new TextEncoder()
    const chunks = [
      'data: {"type":"status","node":"retrieve",',
      '"content":"searching"}\n\ndata: {"type":"token","content":"hello"}\n',
      '\ndata: {"type":"result","content":"hello","citations":[],',
      '"trace_id":"trace-1","failure_type":"none"}\n\n',
      'data: {"type":"done","trace_id":"trace-1"}\n\n',
    ]
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        chunks.forEach((chunk) => controller.enqueue(encoder.encode(chunk)))
        controller.close()
      },
    })
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(body, { status: 200 })),
    )
    const events: StreamEvent[] = []

    await streamChat(
      { message: 'question', user_id: 'user-1', session_id: 'session-1' },
      (event) => events.push(event),
    )

    expect(events.map((event) => event.type)).toEqual([
      'status',
      'token',
      'result',
      'done',
    ])
  })

  it('surfaces FastAPI detail messages', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(JSON.stringify({ detail: 'message exceeds budget' }), {
            status: 422,
            headers: { 'Content-Type': 'application/json' },
          }),
      ),
    )

    await expect(
      streamChat(
        { message: 'question', user_id: 'user-1', session_id: 'session-1' },
        () => undefined,
      ),
    ).rejects.toMatchObject({
      name: 'ApiError',
      message: 'message exceeds budget',
      status: 422,
    })
  })

  it('rejects a partial answer when the stream ends without a result event', async () => {
    const encoder = new TextEncoder()
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          encoder.encode(
            'data: {"type":"token","content":"partial"}\n\n' +
              'data: {"type":"status","node":"error","content":"failed"}\n\n' +
              'data: {"type":"done","trace_id":"trace-2"}\n\n',
          ),
        )
        controller.close()
      },
    })
    vi.stubGlobal('fetch', vi.fn(async () => new Response(body, { status: 200 })))

    await expect(
      streamChat(
        { message: 'question', user_id: 'user-1', session_id: 'session-1' },
        () => undefined,
      ),
    ).rejects.toThrow('未返回经过校验的最终结果')
  })
})

describe('uploadKnowledge', () => {
  it('sends only provided document metadata as multipart form fields', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      const body = init?.body as FormData
      expect(body.get('title')).toBe('制度')
      expect(body.get('source')).toBe('internal_upload')
      expect(body.get('department')).toBe('Finance')
      expect(body.get('version')).toBe('v2')
      expect(body.get('status')).toBe('active')
      expect(body.get('effective_from')).toBeNull()
      return new Response(JSON.stringify({
        message: 'ok',
        record: { title: '制度', source: 'internal_upload' },
      }), { status: 200, headers: { 'Content-Type': 'application/json' } })
    })
    vi.stubGlobal('fetch', fetchMock)

    await uploadKnowledge(
      new File(['content'], 'policy.md'),
      '制度',
      'internal_upload',
      { department: 'Finance', version: 'v2', status: 'active', effective_from: '' },
    )

    expect(fetchMock).toHaveBeenCalledOnce()
  })
})
