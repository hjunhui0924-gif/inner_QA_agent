/** Keep JS interaction breakpoints aligned with the CSS contract. */
export const BREAKPOINTS = {
  mobile: 760,
  evidenceInline: 1280,
} as const

export function isMobileViewport(width = window.innerWidth): boolean {
  return width <= BREAKPOINTS.mobile
}
