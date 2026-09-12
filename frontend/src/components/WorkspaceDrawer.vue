<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Close } from '@element-plus/icons-vue'
import { registerOverlay } from '../utils/overlayStack'

const props = defineProps<{ open: boolean; title: string }>()
const emit = defineEmits<{ close: [] }>()
const panel = ref<HTMLElement | null>(null)
const closeButton = ref<HTMLButtonElement | null>(null)
let previousFocus: HTMLElement | null = null
let overlay: ReturnType<typeof registerOverlay> | null = null

function handleKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    event.preventDefault()
    emit('close')
  }
  if (event.key !== 'Tab') return
  const controls = [
    ...(panel.value?.querySelectorAll<HTMLElement>(
      'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary, a[href], [tabindex="0"]',
    ) ?? []),
  ].filter(
    (element) =>
      element.getClientRects().length > 0 &&
      !element.closest('[hidden], [inert]') &&
      (!element.closest('details:not([open])') || element.matches('summary')),
  )
  const first = controls[0] ?? closeButton.value
  const last = controls.at(-1) ?? first
  if (!first || !last) return
  if (
    !panel.value?.contains(document.activeElement) ||
    (!event.shiftKey && document.activeElement === last)
  ) {
    event.preventDefault()
    first.focus()
  } else if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last.focus()
  }
}

watch(
  () => props.open,
  async (open) => {
    overlay?.setOpen(open)
    if (open) {
      previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
      await nextTick()
      closeButton.value?.focus()
    } else {
      await nextTick()
      if (previousFocus?.isConnected) previousFocus.focus()
    }
  },
)
onMounted(() => {
  overlay = registerOverlay(handleKeydown, { modal: true })
  overlay.setOpen(props.open)
  if (props.open) void nextTick(() => closeButton.value?.focus())
})
onBeforeUnmount(() => overlay?.unregister())
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="workspace-drawer-shell">
      <div class="workspace-drawer-scrim" @click="emit('close')" />
      <section
        ref="panel"
        class="workspace-drawer"
        role="dialog"
        aria-modal="true"
        :aria-label="title"
      >
        <header class="detail-drawer-header">
          <div>
            <p class="eyebrow">知识库</p>
            <h2>{{ title }}</h2>
          </div>
          <button
            ref="closeButton"
            class="icon-button"
            type="button"
            :aria-label="`关闭${title}`"
            @click="emit('close')"
          >
            <el-icon aria-hidden="true"><Close /></el-icon>
          </button>
        </header>
        <div class="workspace-drawer-body"><slot /></div>
      </section>
    </div>
  </Teleport>
</template>
