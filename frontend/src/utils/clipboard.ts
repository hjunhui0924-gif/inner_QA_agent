/** Copy only on an explicit user action, including non-HTTPS intranet deployments. */
export async function copyText(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return
    } catch {
      // Browser policy can deny the modern API while allowing a user-initiated copy.
    }
  }
  const previous = document.activeElement
  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.readOnly = true
  textarea.style.cssText = 'position:fixed;opacity:0;pointer-events:none'
  document.body.append(textarea)
  try {
    textarea.select()
    if (!document.execCommand('copy')) throw new Error('复制失败，请选择文字后复制。')
  } finally {
    textarea.remove()
    if (previous instanceof HTMLElement) previous.focus({ preventScroll: true })
  }
}
