<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import type { FeedbackCategory } from '../types/api'
import { registerOverlay } from '../utils/overlayStack'

const props = defineProps<{
  open: boolean
  submitting: boolean
  error: string
  returnFocusTo?: HTMLElement | null
}>()
const category = defineModel<FeedbackCategory>('category', { required: true })
const note = defineModel<string>('note', { required: true })
const emit = defineEmits<{ close: []; submit: [] }>()
const panel = ref<HTMLFormElement | null>(null)
const descriptionId = `feedback-description-${useId()}`
const categories: Array<{ value: FeedbackCategory; label: string }> = [
  { value: 'incorrect_answer', label: '回答不正确' },
  { value: 'citation_mismatch', label: '引用与回答不匹配' },
  { value: 'outdated_policy', label: '制度内容已过期' },
  { value: 'not_helpful', label: '没有解决问题' },
  { value: 'other', label: '其他' },
]
let previousFocus: HTMLElement | null = null
let overlay: ReturnType<typeof registerOverlay> | null = null

function focusInitial(): void {
  const target = panel.value?.querySelector<HTMLInputElement>('input[type="radio"]:checked')
  ;(target ?? panel.value)?.focus({ preventScroll: true })
}

function handleKeydown(event: KeyboardEvent): void {
  if (!props.open || event.isComposing) return
  if (event.key === 'Escape') {
    event.preventDefault()
    emit('close')
    return
  }
  if (event.key !== 'Tab') return
  // A native radio group has one Tab stop; arrow keys choose the other options.
  const controls = [...(panel.value?.querySelectorAll<HTMLElement>('input, textarea, button') ?? [])]
    .filter(element => element.tabIndex >= 0 && !element.matches(':disabled')
      && element.getClientRects().length > 0 && !element.closest('[inert], [hidden]')
      && (!(element instanceof HTMLInputElement) || element.type !== 'radio' || element.checked))
  const first = controls[0]
  const last = controls.at(-1)
  if (!first || !last) {
    event.preventDefault()
    panel.value?.focus({ preventScroll: true })
    return
  }
  if (!controls.includes(document.activeElement as HTMLElement)) {
    event.preventDefault()
    ;(event.shiftKey ? last : first).focus({ preventScroll: true })
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first.focus({ preventScroll: true })
  } else if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last.focus({ preventScroll: true })
  }
}

watch(() => props.open, async (open, _previous, onCleanup) => {
  overlay?.setOpen(open)
  if (open) previousFocus = props.returnFocusTo
    ?? (document.activeElement instanceof HTMLElement ? document.activeElement : null)
  let cancelled = false
  onCleanup(() => { cancelled = true })
  await nextTick()
  if (cancelled) return
  if (open) focusInitial()
  else if (previousFocus?.isConnected && !previousFocus.closest('[inert]')) {
    previousFocus.focus({ preventScroll: true })
  }
}, { immediate: true })

onMounted(() => {
  overlay = registerOverlay(handleKeydown, { modal: true })
  overlay.setOpen(props.open)
})
onBeforeUnmount(() => overlay?.unregister())
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="feedback-dialog-shell">
      <div class="feedback-dialog-scrim" aria-hidden="true" @click="emit('close')" />
      <form
        ref="panel"
        class="feedback-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="反馈回答"
        :aria-describedby="descriptionId"
        tabindex="-1"
        @submit.prevent="emit('submit')"
      >
        <h2>反馈这条回答</h2>
        <p :id="descriptionId">你的反馈只用于改进回答质量。</p>
        <fieldset class="feedback-categories">
          <legend class="sr-only">反馈类型</legend>
          <label v-for="item in categories" :key="item.value" class="feedback-option">
            <input v-model="category" type="radio" name="feedback-category" :value="item.value" />
            {{ item.label }}
          </label>
        </fieldset>
        <textarea v-model="note" maxlength="2000" aria-label="补充说明（可选）" placeholder="补充说明（可选）" />
        <p v-if="error" class="field-error" role="alert">{{ error }}</p>
        <div class="feedback-actions">
          <button type="button" class="text-button" @click="emit('close')">取消</button>
          <button type="submit" class="primary-button" :disabled="submitting">{{ submitting ? '提交中…' : '提交反馈' }}</button>
        </div>
      </form>
    </div>
  </Teleport>
</template>
