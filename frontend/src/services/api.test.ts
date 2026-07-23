import { afterEach, describe, expect, it, vi } from 'vitest'

import { streamChat } from './api'
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
