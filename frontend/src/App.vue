<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import AppSidebar from './components/AppSidebar.vue'
import ToastStack from './components/ToastStack.vue'
import { useWorkspace } from './composables/useWorkspace'
import { hasOpenModal } from './utils/overlayStack'

const route = useRoute()
const sidebarOpen = ref(false)
const modalOpen = ref(false)
const mobileViewport = ref(false)
const mobileMenuButton = ref<HTMLButtonElement | null>(null)
const mainContent = ref<HTMLElement | null>(null)
const workspace = useWorkspace()

const mainIsolated = computed(() => modalOpen.value || (mobileViewport.value && sidebarOpen.value))

function updateViewport(): void {
  mobileViewport.value = window.innerWidth <= 760
}

onMounted(() => {
  updateViewport()
  window.addEventListener('resize', updateViewport)
  void workspace.initialize()
})

onBeforeUnmount(() => window.removeEventListener('resize', updateViewport))

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
  async () => {
    closeSidebar()
    await nextTick()
    mainContent.value?.focus()
  },
)
</script>

<template>
  <div class="app-shell">
    <a
      class="skip-link"
      href="#main-content"
      :aria-hidden="modalOpen || hasOpenModal ? 'true' : undefined"
      :inert="modalOpen || hasOpenModal ? true : undefined"
    >跳转到主要内容</a>
    <AppSidebar
      :open="sidebarOpen"
      :blocked="hasOpenModal"
      @close="closeSidebar"
      @modal-change="modalOpen = $event"
    />

    <div
      class="mobile-bar"
      :aria-hidden="modalOpen || hasOpenModal ? 'true' : undefined"
      :inert="modalOpen || hasOpenModal ? true : undefined"
    >
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
      <span class="mobile-title"><img src="/knowledge-assistant.png" alt="" width="22" height="22" /> 知识助手</span>
    </div>

    <main
      ref="mainContent"
      id="main-content"
      tabindex="-1"
      class="workspace"
      :aria-hidden="mainIsolated ? 'true' : undefined"
      :inert="mainIsolated ? true : undefined"
    >
      <RouterView />
    </main>
    <ToastStack :blocked="modalOpen || hasOpenModal" />
  </div>
</template>
