<script setup lang="ts">
import { computed, defineAsyncComponent } from 'vue'
import { useRoute } from 'vue-router'
import ToastStack from './components/ToastStack.vue'
import { hasOpenModal } from './utils/overlayStack'
const route = useRoute()
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
