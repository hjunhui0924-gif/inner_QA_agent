import DOMPurify from 'dompurify'
import { marked, Renderer } from 'marked'
import type { Tokens } from 'marked'

marked.use({
  breaks: true,
  gfm: true,
})

const citationPattern = /\[C\d+\]/gi
const htmlEscapes: Record<string, string> = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (character) => htmlEscapes[character])
}

function normalizeCitationIds(citationIds: Iterable<string>): Set<string> {
  return new Set(
    [...citationIds]
      .map((citationId) => citationId.trim().toUpperCase())
      .filter(Boolean),
  )
}

export function renderCitationText(
  text: string,
  citationIds: Iterable<string>,
): string {
  const allowedCitationIds = normalizeCitationIds(citationIds)
  return escapeHtml(text).replace(citationPattern, (marker) => {
    const citationId = marker.slice(1, -1).toUpperCase()
    if (!allowedCitationIds.has(citationId)) return marker
    return (
      `<button type="button" class="inline-citation" `
      + `data-citation-id="${citationId}" `
      + `aria-label="查看引用 ${citationId}">${citationId}</button>`
    )
  })
}

function citationRenderer(citationIds: Iterable<string>): Renderer {
  const renderer = new Renderer()
  renderer.text = function renderText(token: Tokens.Text | Tokens.Escape): string {
    if ('tokens' in token && token.tokens) {
      return this.parser.parseInline(token.tokens)
    }
    return renderCitationText(token.text, citationIds)
  }
  return renderer
}

function sanitizeHtml(html: string): string {
  const candidate = DOMPurify as unknown as {
    sanitize?: (value: string, config: Record<string, unknown>) => string
  }
  if (typeof candidate.sanitize === 'function') {
    return candidate.sanitize(html, {
      ADD_TAGS: ['button'],
      ADD_ATTR: ['data-citation-id'],
    })
  }

  if (typeof DOMPurify === 'function') {
    const purifier = (DOMPurify as unknown as () => {
      sanitize: (value: string, config: Record<string, unknown>) => string
    })()
    return purifier.sanitize(html, {
      ADD_TAGS: ['button'],
      ADD_ATTR: ['data-citation-id'],
    })
  }

  throw new Error('DOMPurify is not available.')
}

export function renderMarkdown(
  content: string,
  citationIds: Iterable<string> = [],
): string {
  const rendered = marked.parse(content, {
    async: false,
    renderer: citationRenderer(citationIds),
  })
  return sanitizeHtml(typeof rendered === 'string' ? rendered : '')
}
