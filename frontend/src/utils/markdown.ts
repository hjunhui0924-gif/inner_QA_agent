import DOMPurify from 'dompurify'
import { marked } from 'marked'

marked.use({
  breaks: true,
  gfm: true,
})

export function renderMarkdown(content: string): string {
  const rendered = marked.parse(content, { async: false })
  return DOMPurify.sanitize(typeof rendered === 'string' ? rendered : '')
}
