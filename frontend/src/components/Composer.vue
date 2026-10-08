<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch, type Component } from 'vue'
import { ArrowDown, ArrowUp, Collection, Right, Search } from '@element-plus/icons-vue'

import ContextMeter from './ContextMeter.vue'
import type { ContextUsage, ChatMode } from '../types/api'

export interface ComposerMode {
  id: ChatMode
  label: string
  description: string
  icon: Component
}

export interface ComposerPrompt {
  title: string
  text: string
  icon: Component
}

const props = defineProps<{
  loadingHistory: boolean
  sending: boolean
  composing: boolean
  canSend: boolean
  modeLocked: boolean
  messagesLength: number
  nearBottom: boolean
  navigationPaused: boolean
  editCandidate: string | null
  copyError: string
  contextUsage: ContextUsage | null
  contextUsageLoading: boolean
  contextUsageStale: boolean
  modes: readonly ComposerMode[]
  prompts: readonly ComposerPrompt[]
}>()

const draft = defineModel<string>('draft', { required: true })
const chatMode = defineModel<ChatMode>('chatMode', { required: true })
const webSearchEnabled = defineModel<boolean>('webSearchEnabled', { required: true })
const emit = defineEmits<{
  submit: [event?: KeyboardEvent]
  cancel: []
  latest: []
  'composition-start': []
  'composition-end': []
  'replace-draft': []
  'keep-draft': []
  'use-prompt': [text: string]
  'select-mode': [mode: ChatMode]
}>()

const field = ref<HTMLTextAreaElement | null>(null)

function resize(): void {
  const textarea = field.value
  if (!textarea) return
  textarea.style.height = 'auto'
  const maxHeight = Math.min(240, Math.floor(window.innerHeight * 0.3))
  textarea.style.height = `${Math.min(Math.max(textarea.scrollHeight, 52), maxHeight)}px`
  textarea.style.overflowY = textarea.scrollHeight > maxHeight ? 'auto' : 'hidden'
}

function handleKeydown(event: KeyboardEvent): void {
  if (event.repeat && (event.ctrlKey || event.metaKey) && event.key === 'Enter') event.preventDefault()
}

function submitFromKeyboard(event: KeyboardEvent): void {
  if (event.isComposing || event.repeat || props.composing) return
  emit('submit', event)
}

function focus(): void { field.value?.focus() }
defineExpose({ focus, resize })

watch(() => draft.value, () => void nextTick(resize))
onMounted(() => {
  window.addEventListener('resize', resize)
  void nextTick(resize)
})
onBeforeUnmount(() => window.removeEventListener('resize', resize))
</script>

<template>
  <div class="composer-wrap">
    <button
      v-if="(!nearBottom || navigationPaused) && messagesLength"
      class="jump-latest secondary-button"
      type="button"
      @click="emit('latest')"
    >
      <el-icon aria-hidden="true"><ArrowDown /></el-icon> 回到最新回答
    </button>
    <p v-if="copyError" class="field-error" role="alert">{{ copyError }}</p>
    <div v-if="editCandidate !== null" class="draft-confirm" role="status">
      输入框已有草稿，是否替换为原问题？
      <button type="button" class="text-button" @click="emit('replace-draft')">替换草稿</button>
      <button type="button" class="text-button" @click="emit('keep-draft')">保留草稿</button>
    </div>
    <form class="composer" @submit.prevent="emit('submit')">
      <label for="question" class="sr-only">{{ chatMode === 'knowledge' ? '向企业知识库提问' : '向通用助手提问' }}</label>
      <textarea
        id="question"
        ref="field"
        name="question"
        autocomplete="off"
        v-model="draft"
        rows="2"
        maxlength="12000"
        :placeholder="chatMode === 'knowledge' ? '例如：差旅报销超过 5000 元需要谁审批？' : '例如：帮我整理一份产品发布会清单。'"
        :disabled="loadingHistory"
        @compositionstart="emit('composition-start')"
        @compositionend="emit('composition-end')"
        @input="resize"
        @keydown="handleKeydown"
        @keydown.ctrl.enter.prevent="submitFromKeyboard($event)"
        @keydown.meta.enter.prevent="submitFromKeyboard($event)"
      />
      <div class="composer-footer">
        <div class="composer-tools">
          <div v-if="!modeLocked" class="mode-switcher" role="group" aria-label="对话模式">
            <button
              v-for="mode in modes"
              :key="mode.id"
              class="mode-option"
              :class="{ active: chatMode === mode.id }"
              type="button"
              :aria-pressed="chatMode === mode.id"
              :title="mode.description"
              :disabled="sending"
              @click="emit('select-mode', mode.id)"
            >
              <el-icon aria-hidden="true"><component :is="mode.icon" /></el-icon>
              <span>{{ mode.label }}</span>
            </button>
          </div>
          <button
            v-if="chatMode === 'general'"
            type="button"
            class="search-toggle"
            :class="{ active: webSearchEnabled }"
            :disabled="sending"
            @click="webSearchEnabled = !webSearchEnabled"
            :aria-pressed="webSearchEnabled"
          >
            <el-icon aria-hidden="true"><Search /></el-icon> 联网搜索
          </button>
          <span v-else-if="modeLocked" class="composer-scope"><el-icon aria-hidden="true"><Collection /></el-icon> 企业知识库</span>
        </div>
        <button v-if="sending" class="composer-submit is-stop" type="button" aria-label="停止回答" title="停止回答" @click="emit('cancel')">
          <span class="stop-symbol" aria-hidden="true" />
        </button>
        <button v-else class="composer-submit" type="submit" aria-label="发送" title="发送（Ctrl / ⌘ + Enter）" :disabled="!canSend">
          <el-icon aria-hidden="true"><ArrowUp /></el-icon>
        </button>
      </div>
    </form>
    <div class="composer-meta">
      <span class="composer-meta-left"><ContextMeter :usage="contextUsage" :loading="contextUsageLoading" :stale="contextUsageStale" />{{ chatMode === 'knowledge' ? '基于企业知识库 · 附参考来源' : (webSearchEnabled ? '通用助手 · 联网搜索已开启' : '通用助手') }}</span>
      <span class="composer-shortcut">{{ sending ? '可先编辑下一条问题' : 'Ctrl / ⌘ + Enter 发送' }}</span>
    </div>
    <div v-if="!messagesLength" class="prompt-grid">
      <button v-for="prompt in prompts" :key="prompt.title" type="button" :disabled="sending" @click="emit('use-prompt', prompt.text)">
        <el-icon class="prompt-icon" aria-hidden="true"><component :is="prompt.icon" /></el-icon>
        <strong>{{ prompt.title }}</strong>
        <span>{{ prompt.text }}</span>
        <el-icon class="prompt-action" aria-hidden="true"><Right /></el-icon>
      </button>
    </div>
    <p v-if="draft.length >= 12000" class="field-error">已达到 12000 字上限，请缩短问题。</p>
    <p class="composer-note">{{ chatMode === 'knowledge' ? '回答基于可访问的企业文档，重要事项请核对原文。' : '通用回答可能存在偏差，重要信息请进一步核实。' }}</p>
  </div>
</template>
