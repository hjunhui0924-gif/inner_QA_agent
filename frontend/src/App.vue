<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import AppSidebar from './components/AppSidebar.vue'
import ToastStack from './components/ToastStack.vue'
import { useWorkspace } from './composables/useWorkspace'

const route = useRoute()
const sidebarOpen = ref(false)
const workspace = useWorkspace()

onMounted(() => workspace.initialize())

watch(
  () => route.fullPath,
  () => {
    sidebarOpen.value = false
  },
)
</script>

<template>
  <div class="app-shell">
    <AppSidebar :open="sidebarOpen" @close="sidebarOpen = false" />

    <div class="mobile-bar">
      <button class="text-button mobile-menu" type="button" @click="sidebarOpen = true">
        导航
      </button>
      <span class="mobile-title">企业知识</span>
      <span class="live-tag">LIVE</span>
    </div>

    <main class="workspace">
      <RouterView />
    </main>
    <ToastStack />
  </div>
</template>
