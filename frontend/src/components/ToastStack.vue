<script setup lang="ts">
import { useWorkspace } from '../composables/useWorkspace'

const { toasts, dismissToast } = useWorkspace()
</script>

<template>
  <div class="toast-stack" aria-live="polite">
    <article v-for="toast in toasts" :key="toast.id" class="toast" :class="toast.tone">
      <div>
        <strong>{{ toast.title }}</strong>
        <p v-if="toast.detail">{{ toast.detail }}</p>
      </div>
      <button type="button" aria-label="关闭通知" @click="dismissToast(toast.id)">关闭</button>
    </article>
  </div>
</template>
