/** Highlight only a unique text-run match; no inferred PDF coordinates or HTML. */
export function highlightPdfText(divs: HTMLElement[], texts: string[], query: string): 'located' | 'ambiguous' | 'not_found' | 'no_text' | 'idle' {
  divs.forEach((div, index) => { div.textContent = texts[index] || '' })
  if (!texts.some((text) => text.trim())) return 'no_text'
  const target = query.trim().replace(/\s+/g, ' ')
  if (!target) return 'idle'
  const matches: Array<{ index: number; start: number; end: number }> = []
  for (const [index, text] of texts.entries()) {
    let normalized = ''
    const offsets: number[] = []
    for (let position = 0; position < text.length; position++) {
      const char = text[position]!
      if (/\s/.test(char)) {
        if (!normalized || normalized.endsWith(' ')) continue
        normalized += ' '
      } else normalized += char
      offsets.push(position)
    }
    let start = normalized.indexOf(target)
    while (start >= 0) {
      matches.push({ index, start: offsets[start]!, end: offsets[start + target.length - 1]! + 1 })
      start = normalized.indexOf(target, start + 1)
    }
  }
  if (matches.length > 1) return 'ambiguous'
  const match = matches[0]
  if (!match) return 'not_found'
  const div = divs[match.index]
  if (!div) return 'not_found'
  const text = texts[match.index]!
  const mark = document.createElement('mark')
  mark.textContent = text.slice(match.start, match.end)
  div.replaceChildren(document.createTextNode(text.slice(0, match.start)), mark, document.createTextNode(text.slice(match.end)))
  return 'located'
}
