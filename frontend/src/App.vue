<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import AppSidebar from './components/AppSidebar.vue'
import ToastStack from './components/ToastStack.vue'
import { useWorkspace } from './composables/useWorkspace'

const route = useRoute()
const sidebarOpen = ref(false)
const mobileMenuButton = ref<HTMLButtonElement | null>(null)
const workspace = useWorkspace()

onMounted(() => workspace.initialize())

function openSidebar(): void {
  sidebarOpen.value = true
}

function closeSidebar(): void {
  sidebarOpen.value = false
  if (window.innerWidth <= 760) {
    void nextTick(() => mobileMenuButton.value?.focus())
  }
}

watch(
  () => route.fullPath,
  () => {
    closeSidebar()
  },
)
</script>

<template>
  <div class="app-shell">
    <AppSidebar :open="sidebarOpen" @close="closeSidebar" />

    <div class="mobile-bar">
      <button
        ref="mobileMenuButton"
        class="text-button mobile-menu"
        type="button"
        aria-label="打开导航菜单"
        :aria-expanded="sidebarOpen"
        @click="openSidebar"
      >
        导航
      </button>
      <span class="mobile-title"><img src="/knowledge-assistant.png" alt="" /> 知识助手</span>
    </div>

    <main class="workspace">
      <RouterView />
    </main>
    <ToastStack />
  </div>
</template>
