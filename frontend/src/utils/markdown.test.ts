import { describe, expect, it } from 'vitest'

import { renderCitationText, renderMarkdown } from './markdown'

describe('citation-aware markdown text', () => {
  it('creates buttons only for citation IDs belonging to the current answer', () => {
    const rendered = renderCitationText('依据 [C1] 和 [C999]', ['C1'])

    expect(rendered).toContain('data-citation-id="C1"')
    expect(rendered).toContain('>C1</button>')
    expect(rendered).toContain('[C999]')
    expect(rendered).not.toContain('data-citation-id="C999"')
  })

  it('escapes text before creating safe citation markup', () => {
    const rendered = renderCitationText('<script>alert(1)</script> [C1]', ['C1'])

    expect(rendered).not.toContain('<script>')
    expect(rendered).toContain('&lt;script&gt;alert(1)&lt;/script&gt;')
  })

  it('preserves only safe inline citation buttons after markdown sanitization', () => {
    const rendered = renderMarkdown('依据 [C1] [C999]', ['C1'])

    expect(rendered).toContain('data-citation-id="C1"')
    expect(rendered).not.toContain('data-citation-id="C999"')
    expect(rendered).toContain('[C999]')
  })

  it('removes executable HTML while keeping the answer text', () => {
    const rendered = renderMarkdown('<img src="javascript:alert(1)" onerror="alert(1)">安全内容', [])

    expect(rendered).not.toContain('onerror')
    expect(rendered).not.toContain('javascript:')
    expect(rendered).toContain('安全内容')
  })

  it('does not turn citation-looking text inside code into a button', () => {
    const rendered = renderMarkdown('`[C1]`\n\n```text\n[C1]\n```', ['C1'])

    expect(rendered.match(/data-citation-id="C1"/g)?.length ?? 0).toBe(0)
    expect(rendered).toContain('[C1]')
  })

  it('matches citation markers case-insensitively while emitting canonical IDs', () => {
    const rendered = renderMarkdown('依据 [c1]', ['c1'])

    expect(rendered).toContain('data-citation-id="C1"')
  })
})
