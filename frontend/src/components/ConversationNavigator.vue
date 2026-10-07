<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import { registerOverlay } from '../utils/overlayStack'

export interface ConversationNavItem {
  id: string
  messageId: string
  label: string
  attemptCount: number
}

const props = defineProps<{
  items: ConversationNavItem[]
  activeId?: string | null
  hasOlderPosition?: boolean
}>()

const emit = defineEmits<{
  navigate: [id: string]
  latest: []
}>()

const open = ref(false)
const root = ref<HTMLElement | null>(null)
const toggle = ref<HTMLButtonElement | null>(null)
const list = ref<HTMLElement | null>(null)
const previewId = ref<string | null>(null)
const preview = computed(() => props.items.find((item) => item.id === previewId.value)?.label)
const listId = `conversation-navigator-${useId()}`
let overlay: ReturnType<typeof registerOverlay> | null = null

function close(restoreFocus = false): void {
  open.value = false
  previewId.value = null
  if (restoreFocus) void nextTick(() => toggle.value?.focus())
}

function outsidePointer(event: PointerEvent): void {
  if (open.value && !root.value?.contains(event.target as Node)) close()
}

function focusLeft(event: FocusEvent): void {
  if (event.relatedTarget && !root.value?.contains(event.relatedTarget as Node)) close()
}

function handleListKeydown(event: KeyboardEvent): void {
  const buttons = [...(list.value?.querySelectorAll<HTMLButtonElement>('button') ?? [])]
  const current = buttons.indexOf(document.activeElement as HTMLButtonElement)
  let next: number
  if (event.key === 'Home') next = 0
  else if (event.key === 'End') next = buttons.length - 1
  else if (event.key === 'ArrowDown') next = Math.min(buttons.length - 1, current + 1)
  else if (event.key === 'ArrowUp') next = Math.max(0, current - 1)
  else return
  event.preventDefault()
  buttons[next]?.focus()
}

watch(open, async (shown) => {
  overlay?.setOpen(shown)
  if (!shown) return
  await nextTick()
  if (!open.value) return
  const active = list.value?.querySelector<HTMLButtonElement>('[aria-current="location"]')
  const first = list.value?.querySelector<HTMLButtonElement>('button')
  const target = active || first
  target?.focus()
})

watch(() => props.items.length, (length) => { if (!length) close() })

onMounted(() => {
  overlay = registerOverlay((event) => {
    if (event.key === 'Escape' && open.value) {
      event.preventDefault()
      close(true)
    }
  })
  window.addEventListener('pointerdown', outsidePointer)
})

onBeforeUnmount(() => {
  overlay?.unregister()
  window.removeEventListener('pointerdown', outsidePointer)
})

function choose(id: string): void {
  emit('navigate', id)
  close()
}
</script>

<template>
  <aside v-if="items.length" ref="root" class="conversation-navigator" aria-label="问题导航" @focusout="focusLeft">
    <button
      ref="toggle"
      type="button"
      class="conversation-navigator-toggle"
      :aria-expanded="open"
      aria-label="问题目录"
      :aria-controls="listId"
      @click="open = !open"
    >
      <span>问题目录</span>
      <span class="conversation-navigator-count" aria-hidden="true">{{ items.length }}</span>
    </button>
    <div v-if="open" :id="listId" class="conversation-navigator-popover">
      <div class="conversation-navigator-heading">
        <strong>当前会话的问题</strong>
        <button v-if="hasOlderPosition" type="button" class="text-button" @click="emit('latest'); close(true)">
          回到最新
        </button>
      </div>
      <p v-if="preview" class="conversation-navigator-question-preview">{{ preview }}</p>
      <nav ref="list" aria-label="问题列表" @keydown="handleListKeydown">
        <button
          v-for="item in items"
          :key="item.id"
          type="button"
          class="conversation-navigator-item"
          :class="{ active: item.id === props.activeId }"
          :aria-current="item.id === props.activeId ? 'location' : undefined"
          :aria-label="item.label"
          :title="item.label"
          :data-navigation-id="item.id"
          @focus="previewId = item.id"
          @mouseenter="previewId = item.id"
          @click="choose(item.id)"
        >
          <span>{{ item.label }}</span>
          <small v-if="item.attemptCount > 1">{{ item.attemptCount }} 次尝试</small>
        </button>
      </nav>
    </div>
  </aside>
</template>
