import { describe, expect, it } from 'vitest'
import { highlightPdfText } from './pdfText'

function runs(texts: string[]) {
  const divs = texts.map(() => document.createElement('span'))
  return { divs, texts }
}

describe('PDF text lookup', () => {
  it('maps normalized whitespace and emoji back to the actual text run', () => {
    const { divs, texts } = runs(['前🙂  审批\n需要经理。'])
    expect(highlightPdfText(divs, texts, '🙂 审批 需要经理')).toBe('located')
    expect(divs[0]?.querySelector('mark')?.textContent).toBe('🙂  审批\n需要经理')
    expect(divs[0]?.textContent).toBe(texts[0])
  })

  it('does not choose one occurrence of a repeated phrase', () => {
    const { divs, texts } = runs(['approval approval', 'approval'])
    expect(highlightPdfText(divs, texts, 'approval')).toBe('ambiguous')
    expect(divs.some((div) => div.querySelector('mark'))).toBe(false)
  })

  it('clears old marks and inserts literal text safely', () => {
    const { divs, texts } = runs(['<img src=x onerror=alert(1)> approval'])
    expect(highlightPdfText(divs, texts, '<img src=x onerror=alert(1)>')).toBe('located')
    expect(divs[0]?.querySelector('img')).toBeNull()
    expect(highlightPdfText(divs, texts, 'missing')).toBe('not_found')
    expect(divs[0]?.querySelector('mark')).toBeNull()
    expect(highlightPdfText(divs, texts, '')).toBe('idle')
    expect(divs[0]?.textContent).toBe(texts[0])
  })

  it('reports scanned pages and refuses to infer positions across text runs', () => {
    const empty = runs([' ', ''])
    expect(highlightPdfText(empty.divs, empty.texts, 'approval')).toBe('no_text')
    const split = runs(['Manager approval', 'is required.'])
    expect(highlightPdfText(split.divs, split.texts, 'Manager approval is required.')).toBe('not_found')
    expect(split.divs.some((div) => div.querySelector('mark'))).toBe(false)
  })
})
