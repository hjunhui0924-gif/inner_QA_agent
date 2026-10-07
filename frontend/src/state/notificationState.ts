import { ref } from 'vue'

import type { ToastMessage } from '../types/api'

export function createNotificationState(createId: () => string) {
  const toasts = ref<ToastMessage[]>([])
  const timers = new Map<string, ReturnType<typeof setTimeout>>()

  function dismissToast(id: string): void {
    const timer = timers.get(id)
    if (timer) clearTimeout(timer)
    timers.delete(id)
    toasts.value = toasts.value.filter((toast) => toast.id !== id)
  }

  function notify(
    tone: ToastMessage['tone'],
    title: string,
    detail?: string,
  ): void {
    const toast: ToastMessage = { id: createId(), tone, title, detail }
    toasts.value.push(toast)
    timers.set(toast.id, setTimeout(() => dismissToast(toast.id), 4200))
  }

  function clearNotifications(): void {
    timers.forEach((timer) => clearTimeout(timer))
    timers.clear()
    toasts.value = []
  }

  return { toasts, notify, dismissToast, clearNotifications }
}
