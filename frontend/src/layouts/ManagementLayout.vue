<script setup lang="ts">
import { onMounted } from 'vue'
import { useWorkspace } from '../composables/useWorkspace'
import { hasOpenModal } from '../utils/overlayStack'
onMounted(() => {
  void useWorkspace().initialize('knowledge')
})
</script>
<template>
  <div class="management-layout">
    <nav
      class="management-nav"
      aria-label="工作区导航"
      :inert="hasOpenModal || undefined"
    >
      <RouterLink to="/" class="management-brand"
        ><img
          src="/knowledge-assistant.png"
          alt=""
          width="28"
          height="28"
        />内部知识助手</RouterLink
      >
      <span>知识管理</span
      ><RouterLink to="/chat" class="secondary-button">返回问答</RouterLink>
    </nav>
    <main id="main-content" tabindex="-1"><slot /></main>
  </div>
</template>
<style>
.management-layout {
  min-height: 100dvh;
  background: var(--canvas);
}
.management-nav {
  display: flex;
  align-items: center;
  gap: 24px;
  padding: 16px clamp(16px, 4vw, 56px);
  border-bottom: 1px solid var(--line);
  background: var(--surface);
}
.management-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--ink);
  font-weight: 600;
}
.management-nav > span {
  font-size: 14px;
  color: var(--ink-soft);
  flex: 1;
}
.management-nav > .secondary-button {
  margin-left: auto;
}
@media (max-width: 600px) {
  .management-nav > span {
    display: none;
  }
  .management-nav {
    gap: 12px;
  }
}
</style>
