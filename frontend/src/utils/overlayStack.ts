import { computed, ref } from 'vue'

export type OverlayKeydownHandler = (event: KeyboardEvent) => void

interface OverlayEntry {
  handleKeydown: OverlayKeydownHandler
  modal: boolean
  open: boolean
}

interface OverlayController {
  setOpen(open: boolean): void
  setModal(modal: boolean): void
  unregister(): void
}

const entries = new Map<number, OverlayEntry>()
const openStack: number[] = []
let nextOverlayId = 0
let listening = false
const openModalCount = ref(0)
export const hasOpenModal = computed(() => openModalCount.value > 0)

function removeFromStack(id: number): void {
  const index = openStack.indexOf(id)
  if (index >= 0) openStack.splice(index, 1)
}

function dispatchKeydown(event: KeyboardEvent): void {
  const activeId = openStack.at(-1)
  if (activeId === undefined) return
  entries.get(activeId)?.handleKeydown(event)
}

function stopListeningIfEmpty(): void {
  if (!listening || entries.size > 0) return
  window.removeEventListener('keydown', dispatchKeydown)
  listening = false
}

export function registerOverlay(
  handleKeydown: OverlayKeydownHandler,
  options: { modal?: boolean } = {},
): OverlayController {
  const id = ++nextOverlayId
  const entry: OverlayEntry = {
    handleKeydown,
    modal: options.modal ?? false,
    open: false,
  }
  entries.set(id, entry)
  if (!listening) {
    window.addEventListener('keydown', dispatchKeydown)
    listening = true
  }

  let registered = true
  return {
    setOpen(open: boolean): void {
      if (!registered) return
      if (entry.open === open) return
      entry.open = open
      removeFromStack(id)
      if (open) {
        openStack.push(id)
        if (entry.modal) openModalCount.value += 1
      } else if (entry.modal) {
        openModalCount.value = Math.max(0, openModalCount.value - 1)
      }
    },
    setModal(modal: boolean): void {
      if (!registered || entry.modal === modal) return
      if (entry.open) {
        openModalCount.value += modal ? 1 : -1
        openModalCount.value = Math.max(0, openModalCount.value)
      }
      entry.modal = modal
    },
    unregister(): void {
      if (!registered) return
      registered = false
      if (entry.open && entry.modal) {
        openModalCount.value = Math.max(0, openModalCount.value - 1)
      }
      entry.open = false
      removeFromStack(id)
      entries.delete(id)
      stopListeningIfEmpty()
    },
  }
}
