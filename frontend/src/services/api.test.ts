import { afterEach, describe, expect, it, vi } from 'vitest'

import { downloadKnowledge, fetchPdfSource, fetchKnowledgeRecord, streamChat, uploadKnowledge } from './api'
import type { StreamEvent } from '../types/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

it('unwraps the authorized document detail response and encodes source IDs', async () => {
  const record = { source_id: 'policy/1', content: '文档正文' }
  const fetchMock = vi.fn(async () => new Response(JSON.stringify({ record }), { status: 200 }))
  vi.stubGlobal('fetch', fetchMock)
  await expect(fetchKnowledgeRecord('policy/1')).resolves.toEqual(record)
  expect(fetchMock).toHaveBeenCalledWith('/api/knowledge/records/policy%2F1', undefined)
})

describe('streamChat', () => {
  function streamResponse(body: ReadableStream<Uint8Array>) {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(body)))
  }
  const payload = { message: 'question', user_id: 'user-1', session_id: 'session-1' }
  const final = { type: 'result', content: '经理审批🙂', citations: [], trace_id: 'trace', failure_type: 'none' }
  it('accepts pending steps in the final process summary', async () => {
    const result = { ...final, process_summary: { status: 'complete', elapsed_ms: 10,
      steps: [{ node: 'update_memory', status: 'pending', duration_ms: null }] } }
    streamResponse(new ReadableStream({ start(controller) {
      controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(result)}\n\n`))
      controller.close()
    } }))
    const events: StreamEvent[] = []
    await streamChat(payload, (event) => events.push(event))
    expect(events).toEqual([result])
  })

  it('requires a complete SSE frame before accepting a result', async () => {
    streamResponse(new ReadableStream({ start(controller) {
      controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(final)}`))
      controller.close()
    } }))
    const events: StreamEvent[] = []
    await expect(streamChat(payload, (event) => events.push(event))).rejects.toThrow('最终结果')
    expect(events).toEqual([])
  })

  it('settles a done event without a result as an incomplete request', async () => {
    const cancel = vi.fn()
    const body = new ReadableStream<Uint8Array>({ start(controller) {
      controller.enqueue(new TextEncoder().encode('data: {"type":"done","trace_id":"trace"}\n\n'))
    }, cancel })
    streamResponse(body)
    await expect(streamChat(payload, () => undefined)).rejects.toThrow('最终结果')
    expect(cancel).toHaveBeenCalledOnce()
    expect(body.locked).toBe(false)
  })

  it.each(['\n', '\r\n'])('decodes Chinese bytes, heartbeats and multiline data using %j', async (newline) => {
    const wire = ': heartbeat' + newline + newline + 'event: message' + newline +
      'data: {"type":"result",' + newline + 'data: "content":"经理审批🙂","citations":[],"trace_id":"trace","failure_type":"none"}' + newline + newline
    const bytes = new TextEncoder().encode(wire)
    streamResponse(new ReadableStream({ start(controller) {
      for (const byte of bytes) controller.enqueue(new Uint8Array([byte]))
      controller.close()
    } }))
    const events: StreamEvent[] = []
    await streamChat(payload, (event) => events.push(event))
    expect(events).toEqual([final])
  })

  it.each([
    'invalid JSON', 'null', '[]', '{}',
    JSON.stringify({ ...final, content: null }),
    JSON.stringify({ ...final, citations: [null] }),
    JSON.stringify({ ...final, citations: [{ citation_id: 1, title: 'x', quote: 'y' }] }),
    JSON.stringify({ ...final, process_summary: { steps: 'bad' } }),
    JSON.stringify({ ...final, process_summary: { status: ['complete'], elapsed_ms: 0, steps: [] } }),
    JSON.stringify({ ...final, process_summary: { status: 'complete', elapsed_ms: 0,
      steps: [{ node: 'generate', status: ['complete'], duration_ms: 0 }] } }),
    JSON.stringify({ ...final, context_usage: { used_tokens: 'bad' } }),
    JSON.stringify({ type: 'preview_delta', content: 'draft' }),
    JSON.stringify({ type: 'status', node: 'generate', content: 'running', node_status: 'invalid' }),
    JSON.stringify({ type: 'status', node: 'generate', content: 'running', node_status: ['running'] }),
  ])('rejects an invalid event and cancels the remaining response: %s', async (data) => {
    const cancel = vi.fn()
    const body = new ReadableStream<Uint8Array>({ start(controller) {
      controller.enqueue(new TextEncoder().encode(`data: ${data}\n\n`))
    }, cancel })
    streamResponse(body)
    await expect(streamChat(payload, () => undefined)).rejects.toThrow('SSE')
    expect(cancel).toHaveBeenCalledOnce()
    expect(body.locked).toBe(false)
  })

  it('releases the reader after clean completion', async () => {
    const body = new ReadableStream<Uint8Array>({ start(controller) {
      controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(final)}\n\n`))
      controller.close()
    } })
    streamResponse(body)
    await streamChat(payload, () => undefined)
    expect(body.locked).toBe(false)
  })

  it('settles on done and ignores duplicate results and late provisional events', async () => {
    const cancel = vi.fn()
    const wire = [final, { ...final, content: 'wrong second result' }, { type: 'token', content: 'late' },
      { type: 'status', node: 'error', content: 'late' }, { type: 'done', trace_id: 'trace' }]
      .map((event) => `data: ${JSON.stringify(event)}\n\n`).join('')
    const body = new ReadableStream<Uint8Array>({ start(controller) { controller.enqueue(new TextEncoder().encode(wire)) }, cancel })
    streamResponse(body)
    const events: StreamEvent[] = []
    const job = streamChat(payload, (event) => events.push(event))
    await vi.waitFor(() => expect(cancel).toHaveBeenCalledOnce())
    await job
    expect(events.map((event) => event.type)).toEqual(['result', 'done'])
  })

  it('does not hide a consumer exception behind a parse error', async () => {
    streamResponse(new ReadableStream({ start(controller) {
      controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(final)}\n\n`)); controller.close()
    } }))
    await expect(streamChat(payload, () => { throw new Error('consumer failed') })).rejects.toThrow('consumer failed')
  })

  it('does not accept a queued result after cancellation', async () => {
    const controller = new AbortController()
    const body = new ReadableStream<Uint8Array>({ start(source) {
      source.enqueue(new TextEncoder().encode('data: {"type":"token","content":"candidate"}\n\n' + `data: ${JSON.stringify(final)}\n\n`))
      source.close()
    } })
    streamResponse(body)
    const events: StreamEvent[] = []
    await expect(streamChat(payload, (event) => { events.push(event); controller.abort() }, controller.signal)).rejects.toMatchObject({ name: 'AbortError' })
    expect(events.map((event) => event.type)).toEqual(['token'])
    expect(body.locked).toBe(false)
  })

  it('cancels a pending body read when the caller aborts', async () => {
    const controller = new AbortController()
    const cancel = vi.fn()
    const body = new ReadableStream<Uint8Array>({ cancel })
    streamResponse(body)
    const job = streamChat(payload, () => undefined, controller.signal)
    const rejection = expect(job).rejects.toMatchObject({ name: 'AbortError' })
    await vi.waitFor(() => expect(body.locked).toBe(true))
    controller.abort()
    await rejection
    expect(cancel).toHaveBeenCalledOnce()
    expect(body.locked).toBe(false)
  })

  it.each([false, true])('handles a broken transport (committed result: %s)', async (committed) => {
    let source!: ReadableStreamDefaultController<Uint8Array>
    const body = new ReadableStream<Uint8Array>({ start(controller) { source = controller } })
    streamResponse(body)
    const events: StreamEvent[] = []
    const job = streamChat(payload, (event) => events.push(event))
    const outcome = committed ? expect(job).resolves.toBeUndefined() : expect(job).rejects.toThrow('network failure')
    if (committed) {
      source.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(final)}\n\n`))
      await vi.waitFor(() => expect(events).toHaveLength(1))
    }
    source.error(new Error('network failure'))
    await outcome
    expect(body.locked).toBe(false)
    expect(fetch).toHaveBeenCalledOnce()
  })

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

describe('downloadKnowledge', () => {
  it('downloads an authorized source as a blob', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response('file body', { status: 200 }),
    ))

    const blob = await downloadKnowledge('finance policy')

    expect(blob).toBeInstanceOf(Blob)
    expect(fetch).toHaveBeenCalledWith('/api/knowledge/records/finance%20policy/download')
  })
})

describe('bounded PDF preview download', () => {
  it('streams authorized bytes and respects configured limits', async () => {
    const signal = new AbortController().signal
    const body = new ReadableStream<Uint8Array>({ start(controller) {
      controller.enqueue(new Uint8Array([1, 2]))
      controller.enqueue(new Uint8Array([3, 4]))
      controller.close()
    } })
    const fetchMock = vi.fn().mockResolvedValue(new Response(body, { headers: {
      'X-Pdf-Preview-Max-Bytes': '4', 'X-Pdf-Preview-Max-Pages': '20',
    } }))
    vi.stubGlobal('fetch', fetchMock)
    await expect(fetchPdfSource('policy/1', signal)).resolves.toEqual({ bytes: new Uint8Array([1, 2, 3, 4]), maxPages: 20 })
    expect(fetchMock).toHaveBeenCalledWith('/api/knowledge/records/policy%2F1/download?preview=true', { signal })
  })

  it.each([true, false])('cancels an oversized response (declared length: %s)', async (declared) => {
    const cancelled = vi.fn()
    const body = new ReadableStream<Uint8Array>({ start(controller) {
      controller.enqueue(new Uint8Array(5))
    }, cancel: cancelled })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(body, { headers: {
      'X-Pdf-Preview-Max-Bytes': '4', ...(declared ? { 'Content-Length': '5' } : {}),
    } })))
    await expect(fetchPdfSource('policy', new AbortController().signal)).rejects.toMatchObject({ status: 413 })
    expect(cancelled).toHaveBeenCalledOnce()
    expect(body.locked).toBe(false)
  })

  it('uses safe defaults when limit headers are invalid', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('pdf', { headers: {
      'X-Pdf-Preview-Max-Bytes': '-1', 'X-Pdf-Preview-Max-Pages': 'NaN',
    } })))
    expect((await fetchPdfSource('policy', new AbortController().signal)).maxPages).toBe(500)
  })

  it('preserves authorization errors and transport aborts', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: 'denied' }), { status: 403 })))
    await expect(fetchPdfSource('policy', new AbortController().signal)).rejects.toMatchObject({ status: 403 })
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new DOMException('cancelled', 'AbortError')))
    await expect(fetchPdfSource('policy', new AbortController().signal)).rejects.toMatchObject({ name: 'AbortError' })
  })
})
