<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type * as PdfJs from 'pdfjs-dist'
import { fetchPdfSource } from '../services/api'
import { highlightPdfText } from '../utils/pdfText'

const props = defineProps<{ sourceId: string; open: boolean; initialPage?: number | null; initialQuote?: string }>()
const canvas = ref<HTMLCanvasElement | null>(null)
const scroll = ref<HTMLElement | null>(null)
const textContainer = ref<HTMLElement | null>(null)
const loading = ref(false)
const rendering = ref(false)
const error = ref('')
const page = ref(1)
const pages = ref(0)
const zoom = ref(1)
const pageWidth = ref(0)
const pageHeight = ref(0)
const pageScale = ref(1)
const query = ref('')
const locationStatus = ref<ReturnType<typeof highlightPdfText>>('idle')
const pageWarning = ref('')
const locationMessage = computed(() => ({
  located: '已在当前页文本层定位到唯一匹配，请核对原页。',
  ambiguous: '当前页存在多处匹配，未标注，请手动核对。',
  not_found: '已打开该页，未能精确标注，可核对抽取文本。',
  no_text: '未提取到可定位文本，可查看图像页或抽取文本。',
  idle: '',
})[locationStatus.value])
let generation = 0
let renderGeneration = 0
let abort: AbortController | null = null
let pdfjsLib: typeof PdfJs | null = null
let documentTask: PdfJs.PDFDocumentLoadingTask | null = null
let documentRef: PdfJs.PDFDocumentProxy | null = null
let ownedWorker: Worker | null = null
let workerAdapter: PdfJs.PDFWorker | null = null
let workerFailure: Promise<never> | null = null
let rejectWorker: ((error: Error) => void) | null = null
let renderTask: PdfJs.RenderTask | null = null
let textLayer: PdfJs.TextLayer | null = null
let renderQueue: Promise<void> = Promise.resolve()
let resizeObserver: ResizeObserver | null = null
let observedWidth = 0

function locateText(): void {
  if (!textLayer || rendering.value) return
  locationStatus.value = highlightPdfText(textLayer.textDivs, textLayer.textContentItemsStr, query.value)
}

function withWorker<T>(operation: Promise<T>): Promise<T> {
  return workerFailure ? Promise.race([operation, workerFailure]) : operation
}

function releaseDocument(): void {
  ++generation
  ++renderGeneration
  abort?.abort()
  abort = null
  renderTask?.cancel()
  textLayer?.cancel()
  renderTask = null
  textLayer = null
  const task = documentTask
  const worker = ownedWorker
  const adapter = workerAdapter
  rejectWorker?.(new DOMException('Preview closed', 'AbortError'))
  rejectWorker = null
  workerFailure = null
  ownedWorker = null
  workerAdapter = null
  documentTask = null
  documentRef = null
  const cleanupWorker = () => { adapter?.destroy(); worker?.terminate() }
  // Release even if a broken worker cannot acknowledge document termination.
  if (task) {
    const fallback = window.setTimeout(cleanupWorker, 1000)
    void task.destroy().catch(() => undefined).finally(() => { window.clearTimeout(fallback); cleanupWorker() })
  } else cleanupWorker()
  textContainer.value?.replaceChildren()
  loading.value = false
  rendering.value = false
  pages.value = 0
  page.value = 1
  query.value = ''
  pageWidth.value = 0
  pageHeight.value = 0
  locationStatus.value = 'idle'
}

function scheduleRender(): Promise<void> {
  const currentDocument = documentRef
  if (!currentDocument || !pdfjsLib) return Promise.resolve()
  const currentGeneration = generation
  const request = ++renderGeneration
  const requestedPage = page.value
  const requestedZoom = zoom.value
  rendering.value = true
  error.value = ''
  locationStatus.value = 'idle'
  renderTask?.cancel()
  textLayer?.cancel()
  const valid = () => currentGeneration === generation && request === renderGeneration && props.open
  // Wait for cancelled tasks before reusing the canvas, also across reopenings.
  renderQueue = renderQueue.then(async () => {
    if (!valid()) return
    try {
      await nextTick()
      const currentPage = await withWorker(currentDocument.getPage(requestedPage))
      if (!valid() || !canvas.value || !textContainer.value || !scroll.value || !pdfjsLib) return
      const base = currentPage.getViewport({ scale: 1 })
      const availableWidth = Math.max(1, scroll.value.clientWidth - 24)
      const viewport = currentPage.getViewport({ scale: availableWidth / base.width * requestedZoom })
      if (viewport.width > 8192 || viewport.height > 8192 || viewport.width * viewport.height > 16_000_000) throw new Error('canvas_limit')
      pageWidth.value = viewport.width
      pageHeight.value = viewport.height
      pageScale.value = viewport.scale
      textContainer.value.replaceChildren()
      const context = canvas.value.getContext('2d')
      if (!context) throw new Error('canvas unavailable')
      canvas.value.width = Math.ceil(viewport.width)
      canvas.value.height = Math.ceil(viewport.height)
      renderTask = currentPage.render({ canvas: canvas.value, canvasContext: context, viewport })
      await withWorker(renderTask.promise)
      if (!valid()) return
      const content = await withWorker(currentPage.getTextContent())
      if (!valid()) return
      textLayer = new pdfjsLib.TextLayer({ textContentSource: content, container: textContainer.value, viewport })
      await withWorker(textLayer.render())
      if (!valid()) return
      rendering.value = false
      locateText()
      scroll.value.scrollTop = 0
      scroll.value.scrollLeft = 0
    } catch (failure) {
      if (valid()) console.warn('PDF page rendering failed', failure)
      if (valid()) error.value = 'PDF 页面无法显示，请重试或查看抽取文本、下载原文件。'
    } finally {
      if (valid()) rendering.value = false
    }
  })
  return renderQueue
}

function targetPage(): void {
  const target = props.initialPage
  const valid = target != null && Number.isInteger(target) && target >= 1 && target <= pages.value
  page.value = valid ? target : 1
  pageWarning.value = target != null && !valid ? '引用页码超出原文件范围，已打开第一页。' : ''
  query.value = props.initialQuote || ''
}

async function openDocument(): Promise<void> {
  releaseDocument()
  const currentGeneration = generation
  loading.value = true
  error.value = ''
  pageWarning.value = ''
  zoom.value = 1
  const controller = new AbortController()
  abort = controller
  try {
    const [library, worker, source] = await Promise.all([
      import('pdfjs-dist/legacy/build/pdf.mjs'),
      import('pdfjs-dist/legacy/build/pdf.worker.min.mjs?url'),
      fetchPdfSource(props.sourceId, controller.signal),
    ])
    if (currentGeneration !== generation) return
    pdfjsLib = library
    // A dedicated worker also keeps a failed startup from poisoning retries via
    // pdf.js's shared fake-worker cache. Never parse enterprise PDFs on a server.
    const nativeWorker = new Worker(new URL(worker.default, window.location.href), { type: 'module' })
    ownedWorker = nativeWorker
    workerFailure = new Promise<never>((_resolve, reject) => { rejectWorker = reject })
    void workerFailure.catch(() => undefined)
    const failWorker = rejectWorker
    nativeWorker.addEventListener('error', () => failWorker?.(new Error('worker_failed')), { once: true })
    workerAdapter = library.PDFWorker.create({ port: nativeWorker })
    const resourceBase = `${import.meta.env.BASE_URL}pdfjs/`
    const task = library.getDocument({ data: source.bytes, enableXfa: false, worker: workerAdapter,
      cMapUrl: `${resourceBase}cmaps/`, cMapPacked: true,
      standardFontDataUrl: `${resourceBase}standard_fonts/`, wasmUrl: `${resourceBase}wasm/` })
    documentTask = task
    const pdf = await withWorker(task.promise)
    if (currentGeneration !== generation) return
    if (pdf.numPages > source.maxPages) throw new Error('page_limit')
    documentRef = pdf
    pages.value = pdf.numPages
    targetPage()
    await nextTick()
    if (currentGeneration !== generation) return
    loading.value = false
    await scheduleRender()
  } catch (failure) {
    if (currentGeneration !== generation) return
    console.warn('PDF preview loading failed', failure)
    const name = failure instanceof Error ? failure.name : ''
    const message = failure instanceof Error ? failure.message : ''
    const status = failure && typeof failure === 'object' && 'status' in failure ? failure.status : 0
    releaseDocument()
    error.value = status === 403 ? '无权预览该来源。' : status === 404 ? '来源文件不存在或不可访问。'
      : status === 413 ? 'PDF 文件超过预览上限，可下载原文件查看。'
      : message === 'page_limit' ? 'PDF 页数超过预览上限，可下载原文件查看。'
      : name === 'PasswordException' ? 'PDF 已加密，请下载原文件后使用密码打开。'
      : 'PDF 无法加载，请重试或查看抽取文本、下载原文件。'
  } finally {
    if (currentGeneration === generation) loading.value = false
  }
}

watch(() => [props.open, props.sourceId] as const, ([open]) => {
  if (open) void openDocument()
  else releaseDocument()
}, { immediate: true })
watch(() => [page.value, zoom.value], () => { if (!loading.value) void scheduleRender() })
watch(() => [props.initialPage, props.initialQuote], () => {
  if (!documentRef || loading.value) return
  targetPage()
  void scheduleRender()
})
watch(query, locateText)
onMounted(() => {
  resizeObserver = new ResizeObserver(() => {
    const width = scroll.value?.clientWidth || 0
    if (width === observedWidth) return
    observedWidth = width
    if (documentRef && !loading.value) void scheduleRender()
  })
  if (scroll.value) resizeObserver.observe(scroll.value)
})
onBeforeUnmount(() => { resizeObserver?.disconnect(); releaseDocument() })
</script>

<template>
  <section class="pdf-source-preview" aria-label="PDF 原页预览" :aria-busy="loading || rendering">
    <div class="pdf-preview-toolbar">
      <button type="button" class="text-button" :disabled="page <= 1 || loading || !pages" @click="page -= 1">上一页</button>
      <span>{{ page }} / {{ pages || '—' }}</span>
      <button type="button" class="text-button" :disabled="!pages || page >= pages || loading" @click="page += 1">下一页</button>
      <button type="button" class="text-button" :disabled="!pages || zoom >= 2" @click="zoom = Math.min(2, zoom + 0.25)">放大</button>
      <button type="button" class="text-button" :disabled="!pages || zoom <= 0.5" @click="zoom = Math.max(0.5, zoom - 0.25)">缩小</button>
      <button type="button" class="text-button" :disabled="!pages" @click="zoom = 1; scheduleRender()">适应宽度</button>
      <span>{{ Math.round(zoom * 100) }}%</span>
    </div>
    <p class="pdf-preview-note">当前可访问原文件；文件页序可能与正文印刷页码不同。</p>
    <p v-if="pageWarning" class="pdf-preview-note" role="status">{{ pageWarning }}</p>
    <label class="pdf-text-search">当前页查找<input v-model="query" type="search" :disabled="!pages" placeholder="输入原文片段" /></label>
    <p v-if="locationMessage" class="pdf-preview-note" role="status">{{ locationMessage }}</p>
    <p v-if="loading || rendering" class="pdf-preview-note" role="status">{{ loading ? '正在加载 PDF 原页…' : '正在显示页面…' }}</p>
    <div v-if="error" class="pdf-preview-note" role="alert">
      <p class="field-error">{{ error }}</p>
      <button type="button" class="secondary-button" @click="openDocument">重试 PDF 预览</button>
    </div>
    <div v-show="!error" ref="scroll" class="pdf-page-scroll" tabindex="0" aria-label="PDF 页面，可滚动查看">
      <div class="pdf-page" :style="{ width: `${pageWidth}px`, height: `${pageHeight}px`, visibility: loading || rendering ? 'hidden' : 'visible' }">
        <canvas ref="canvas" :aria-label="`原文件第 ${page} 页`" />
        <div ref="textContainer" class="pdf-text-layer" :style="{ '--total-scale-factor': pageScale }" />
      </div>
    </div>
  </section>
</template>

<style scoped>
.pdf-preview-note { padding: 0 12px; font-size: 13px; }
.pdf-preview-toolbar button { min-height: 44px; }
.pdf-text-search { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 8px 12px; font-size: 13px; }
.pdf-text-search input { min-width: 0; flex: 1; min-height: 44px; border: 1px solid var(--line); border-radius: 6px; padding: 6px; background: var(--surface); color: var(--ink); }
.pdf-page { position: relative; margin-inline: auto; background: white; }
.pdf-text-layer { position: absolute; inset: 0; overflow: clip; text-align: initial; line-height: 1; letter-spacing: normal; word-spacing: normal; text-size-adjust: none; forced-color-adjust: none; transform-origin: 0 0; --scale-round-x: 1px; --scale-round-y: 1px; --min-font-size: 1; --text-scale-factor: calc(var(--total-scale-factor) * var(--min-font-size)); --min-font-size-inv: calc(1 / var(--min-font-size)); }
.pdf-text-layer :deep(:is(span, br)) { position: absolute; color: transparent; white-space: pre; cursor: text; transform-origin: 0 0; user-select: text; }
.pdf-text-layer :deep(> :not(.markedContent)), .pdf-text-layer :deep(.markedContent span:not(.markedContent)) { --font-height: 0; font-size: calc(var(--text-scale-factor) * var(--font-height)); --scale-x: 1; --rotate: 0deg; transform: rotate(var(--rotate)) scaleX(var(--scale-x)) scale(var(--min-font-size-inv)); }
.pdf-text-layer :deep(.markedContent) { display: contents; }
.pdf-text-layer :deep(mark) { color: transparent; background: rgb(255 202 40 / 45%); padding: 0; }
.pdf-text-layer :deep(::selection) { background: rgb(0 100 255 / 25%); }
</style>
