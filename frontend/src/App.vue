<script setup lang="ts">
import { computed, defineAsyncComponent, onMounted, onBeforeUnmount } from 'vue'
import { useRoute } from 'vue-router'
import ToastStack from './components/ToastStack.vue'
import { hasOpenModal } from './utils/overlayStack'
const route = useRoute()
// Native selects may match :focus-visible even after a pointer click.
function usePointerFeedback() {
  document.documentElement.dataset.inputModality = 'pointer'
}
function useKeyboardFeedback(event: KeyboardEvent) {
  if (event.metaKey || event.ctrlKey || event.altKey) return
  document.documentElement.dataset.inputModality = 'keyboard'
}
onMounted(() => {
  document.addEventListener('pointerdown', usePointerFeedback, true)
  document.addEventListener('keydown', useKeyboardFeedback, true)
})
onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', usePointerFeedback, true)
  document.removeEventListener('keydown', useKeyboardFeedback, true)
  delete document.documentElement.dataset.inputModality
})
const layouts = {
  chat: defineAsyncComponent(() => import('./layouts/ChatLayout.vue')),
  management: defineAsyncComponent(
    () => import('./layouts/ManagementLayout.vue'),
  ),
  landing: defineAsyncComponent(() => import('./layouts/LandingLayout.vue')),
}
const layout = computed(
  () => layouts[route.meta.layout as keyof typeof layouts] || layouts.landing,
)
</script>
<template>
  <component :is="layout"><RouterView /></component>
  <ToastStack :blocked="hasOpenModal" />
</template>
