<script setup lang="ts">
import type { Citation } from '../types/api'

defineProps<{ open: boolean; citations: Citation[] }>()

const emit = defineEmits<{ close: [] }>()
</script>

<template>
  <aside class="evidence-panel" :class="{ open }" aria-label="引用证据">
    <div class="evidence-header">
      <div>
        <p class="eyebrow">VERIFIABLE ANSWERS</p>
        <h2>引用证据</h2>
      </div>
      <button class="text-button evidence-close" type="button" @click="emit('close')">
        收起
      </button>
    </div>

    <div v-if="citations.length === 0" class="evidence-empty">
      <span class="evidence-number">C1</span>
      <h3>回答后查看原文</h3>
      <p>引用文件、页码、章节与逐字原文将在这里展开，帮助你核对回答依据。</p>
    </div>

    <div v-else class="evidence-list">
      <article v-for="citation in citations" :key="citation.citation_id" class="evidence-card">
        <div class="evidence-card-heading">
          <span class="evidence-number">{{ citation.citation_id }}</span>
          <div>
            <h3>{{ citation.title || '未命名文档' }}</h3>
            <p>
              <span v-if="citation.page">第 {{ citation.page }} 页</span>
              <span v-if="citation.section">{{ citation.section }}</span>
              <span v-if="citation.source">{{ citation.source }}</span>
            </p>
          </div>
        </div>
        <blockquote>{{ citation.quote }}</blockquote>
        <span class="verification-label">{{ citation.verification_status || 'provenance_only' }}</span>
      </article>
    </div>

    <div class="evidence-note">
      出处用于定位原文；回答是否受证据支持，由后端校验流程独立判断。
    </div>
  </aside>
</template>
