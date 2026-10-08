# Vue 企业知识工作台

## 技术方案

默认前端采用：

- Vue 3；
- Vite；
- TypeScript 严格模式；
- Vue Router；
- Element Plus 组件与图标；
- 原生 CSS 设计 Token 与响应式布局。

当前没有引入 Pinia 或 Tailwind。Element Plus 仅按需注册 `ElIcon`，图标从直接依赖 `@element-plus/icons-vue` 导入。按钮、表单与响应式布局由原生 HTML/CSS 实现；三个页面及布局采用动态导入。共享状态由 Composition API 工作区组合函数和 `src/state/` 下的轻量状态/动作模块组织；现有页面规模不需要额外状态库。

## 页面结构

```text
src/
  components/
    AppSidebar.vue
    Composer.vue
    ConversationNavigator.vue
    EvidencePanel.vue
    FeedbackDialog.vue
    MessageItem.vue
    PdfSourcePreview.vue
    WorkspaceDrawer.vue
  composables/
    useConversationScroll.ts
    useEvidenceSelection.ts
    useWorkspace.ts
  state/
    sessionState.ts
    chatState.ts
    knowledgeState.ts
    contextUsageState.ts
    sessionActions.ts
    chatActions.ts
    knowledgeActions.ts
    contextActions.ts
    chatPresentation.ts
  layouts/
    LandingLayout.vue
    ChatLayout.vue
    ManagementLayout.vue
  views/
    HomeView.vue
    ChatView.vue
    KnowledgeView.vue
  styles/
    tokens.css
    base.css
    chat.css
    knowledge.css
    overlays.css
  App.vue
  main.ts
  router.ts
```

- `/`：静态产品首页，示例使用合成资料，不加载 health/sessions/knowledge；
- `/chat`：会话侧栏、知识库/通用模式选择、联网搜索、问答区、快捷问题、输入框和引用证据面板；
- `/knowledge`：文档列表、搜索与筛选优先；点击“添加文档”打开上传抽屉，元数据按需展开；
- 760px 以下使用移动端侧栏；
- 1280px 以下引用面板使用模态抽屉；更宽屏幕保留至少 600px 主阅读区。

`Composer.vue` 负责输入边界、快捷键、模式切换、停止和示例问题；会话、聊天、知识库和上下文用量分别由 `src/state/` 下的状态与动作模块管理，`ChatView.vue` 只负责页面编排。

## 视觉系统

- 背景：浅灰白 `#F8F9FC`，侧栏 `#F3F5F9`，内容面板白色；
- 正文：墨色 `#202634`，辅助文字使用更柔和的灰蓝色；
- 主色：钴蓝 `#3458CF`，用于主要操作、选中状态和引用；
- 标题与正文统一使用系统中文无衬线字体，不依赖外部字体下载；
- 控件 8px、面板 14～16px 圆角，轻阴影，160～220ms 微交互；
- 对话页固定到动态视口高度，消息独立滚动，输入区始终可见；
- 等待状态与可展开过程位于正在回答的消息中，低高度屏幕不挤占固定输入区；
- 支持减少动效、键盘焦点、抽屉焦点循环与 Escape 关闭。

参考来源、取舍与验证数据见 [前端设计说明](../docs/frontend_workspace_redesign.md)。

## 本地开发

```bash
npm ci
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

以上命令在 `frontend/` 目录执行；后端启动与环境配置见 [根目录快速启动](../README.md#快速启动本地开发)。开发地址为 `http://127.0.0.1:5173`。`vite.config.ts` 已预留 `/api` 到
`http://127.0.0.1:8000` 的代理。

检查命令：

```bash
npm run typecheck
npm run test
npm run build
```

## 已实现能力

- Vue TypeScript 工程；
- 桌面问答工作台；
- FastAPI 健康状态与离线提示；
- 会话列表、历史加载、新建、切换和完整删除；
- 根据第一条用户问题生成一句话会话标题，标题在响应结束后由有界后台队列生成；旧标题每次列表读取最多入队两条；
- 新会话选择知识库或通用模式；开始对话后锁定模式，只能开启新对话重新选择；
- 通用模式提供“联网搜索”开关，知识库模式不显示联网入口；
- SSE 流式问答、可信答案状态和可折叠 Agent 处理时间线；
- 按会话及回答绑定的引用快照和历史引用恢复；网页来源可安全打开，标明“搜索服务提供的来源”，无原文时不提供原文复制；
- 知识文件上传、重复提示和知识库列表；
- 知识库文档搜索、部门/状态/版本筛选、固定每页 10 份客户端分页、只读详情抽屉和上传元数据校验；
- 文档详情中的来源下载按钮；下载请求仍由后端执行来源权限校验；
- 会话搜索、加载失败重试、删除后邻近会话选择和移动端焦点管理；
- Markdown 安全渲染、论文式上标 `[1]` 与参考来源列表、引用联动、答案/原文复制、友好失败提示、重试和删除确认；
- 阅读历史时保留滚动位置，提供“回到最新回答”；按会话保存内存草稿与阅读位置，跨任务页和历史切换后恢复；
- 移动端导航与响应式布局，覆盖 375px～1440px 的弹性布局、安全区域和可换行长文本；
- 跳转主要内容、路由切换焦点归位、移动端导航/证据面板/详情抽屉焦点限制、Escape 关闭和焦点回收；
- 图标无障碍标记、当前会话语义、主要独立控件 44px 触控目标（正文引用为紧凑 24px，底部来源提供较大点击区域）、减少动效和必要的 `aria-live` 状态播报；
- Vitest 的 SSE 分片、答案交付状态、引用安全渲染、组合函数和证据面板交互测试；
- 桌面与 390px 移动端手工浏览器联调；根目录 `tests/e2e_mvp_browser.py` 提供 MVP 浏览器验收，使用本机 Chrome/Edge 和确定性 API fixture，不把 `frontend/node_modules`、构建产物或浏览器报告提交到 GitHub。

真实后端联调在项目根目录执行 `python tests/e2e_live_backend.py --live`，使用隔离数据和真实模型，会消耗模型配额。历史验收见 [后端与前端真实接口验证](../docs/backend_frontend_verification.md)；当前测试结果以本次运行输出为准。

## 布局与交互回归

启动 Vite 后，在项目根目录执行：

```bash
python tests/e2e_mvp_browser.py
python tests/e2e_workspace_design.py
python tests/e2e_product_experience.py
python tests/e2e_paper_citations.py
```

第二个脚本覆盖六种常规视口、三种低高度视口、首屏输入区与文档列表、上传抽屉焦点、答案复制、长会话滚动和引用侧栏/手机抽屉。截图保存在忽略提交的 `work/` 目录。浏览器边界注入确定性响应，不调用在线模型，也不写入实际知识库。

## 部署与性能测量

统一浅色系统，答案正文 16px，知识管理使用独立布局。上传状态和表单留在工作区内存，关闭抽屉或跨页不会重复提交；刷新网页不保证恢复任务。首页与两业务路径都可直接访问，部署静态服务器须配置 SPA 回退到 index.html。

开发模式下可在浏览器控制台读取 `window.workspacePerformance.snapshot()` 或调用 `clear()` 清空；记录最多 100 轮，无问题/答案正文。双 requestAnimationFrame 是首次绘制的近似观测，不是浏览器呈现硬件时间。记录不可代替真实后端 benchmark。

确定性验收 `python tests/e2e_product_experience.py`；本机性能脚本 `python tests/benchmark_browser_experience.py` 要求开发服务 5173 和生产 preview 4173 同时运行。真实 HTTP 基准使用 `python scripts/benchmark_chat_latency.py --help`，必须显式 `--live` 并指向隔离后端。

历史验收结果、截图、性能样本与限制见 [实施报告](../docs/product_experience_implementation_report.md)。

补充验收：`python tests/e2e_experience_edge_cases.py` 覆盖真正 200% 标签页缩放、触控模拟、嵌套来源抽屉及权限/断流/取消边界；需要支持扩展的 Playwright Chromium，可通过 `CHROMIUM_EXECUTABLE` 指定其路径。模拟触控不等于真实设备验收。


## 交互行为与验收边界

正文引用按每条回答的 citations 顺序显示 `[1]`、`[2]`；后台 `citation_id` / `evidence_id` 保留用于来源映射，不展示长 ID 或 `<sup>` 标签文本。相同引用重复出现沿用编号，面板摘要和参考来源列表一致。鼠标点击无按钮焦点外圈，键盘导航保留焦点；关闭面板返回原触发器。

普通通用非联网回答允许增量预览，正式结果仍以 result 为准；知识库与联网回答校验后交付。等待阶段可编辑下一条草稿，取消或失败支持重新生成/编辑，重试按问题折叠。联网由用户显式开启，关闭时不会自动触发。

前端回归数量和构建状态以 `npm run test`、`npm run build` 的实际输出为准。2026-10-02 已执行 Chrome、Edge、Firefox、WebKit 核心交互矩阵并修复 WebKit 上传抽屉焦点恢复问题；API fixtures 与触控模拟不能替代真机软键盘或真实模型质量验收。新矩阵入口为 `python tests/e2e_browser_matrix.py --output-dir work/browser-new`（先启动 5173）。具体结果见 [M6 记录](../docs/m6_acceptance_2026-10-02.md)，有效待办见 [当前状态](../docs/current_status.md)。

2026-10-07 DSH M6 SSE/滚动专项已完成：前端 140 项与生产构建通过，四浏览器单批 56/56。新增 `src/utils/streamEvent.ts` 校验流事件，取消和迟到结果隔离、阅读锚点、布局跟随与 End 恢复均经回归。先启动 5173，再在仓库根目录运行 `python tests/e2e_stream_scroll.py --browsers chrome msedge firefox webkit`；范围及失败修复记录见 [DSH M6 验收](../docs/dsh_m6_acceptance_2026-10-07.md)。

同日补齐 PDF 四浏览器 64/64、生产构建 16/16，以及反馈重试四浏览器 20/20。`tooling/pdfAssets.ts` 配合 Vite 提供并打包 pdf.js 的本地字符映射、标准字体、WASM 和许可证，修复部分中文 PDF 文本缺失；原页仍按需加载。反馈同内容失败重试复用 ID，修改内容生成新 ID，旧请求不影响新弹窗。命令为 `python tests/e2e_pdf_source_preview.py --browsers chrome msedge firefox webkit` 与 `python tests/e2e_feedback_retry.py --browsers chrome msedge firefox webkit`；详见 [本轮验收](../docs/dsh_pdf_browser_acceptance_2026-10-07.md)。

M4 反馈弹层的键盘与焦点另通过四浏览器 32/32：`FeedbackDialog.vue` 复用弹层栈处理打开焦点、单选组、Tab/Shift+Tab、顶层 Escape、背景隔离及关闭后的阅读位置保持；请求与重试状态仍由 ChatView 管理。运行 `python tests/e2e_feedback_accessibility.py --browsers chrome msedge firefox webkit`；本轮反馈重试另 20/20、前端 140 项与构建、既有页面回归通过。详见 [M4 键盘与焦点验收](../docs/dsh_m4_accessibility_acceptance_2026-10-07.md)，真机软键盘仍待验收。

2026-10-07 的后端业务场景复测补充了 PDF 软换行下的引用上下文：PB01/PB06 差旅题和 PB09/CX01 跨页规则题均完成隔离真实 Agent 复测。前端 API fixture 和模拟视口不能替代真机输入法、真实内部权限或独立业务审核，统一以 [项目当前状态](../docs/current_status.md) 的范围说明为准。

2026-10-08 new-api 交互优化 Demo 已完成：`Composer.vue` 输入边界拆分、session/chat/knowledge/context 状态与动作模块、聊天状态展示规则、会话和知识列表 Skeleton、复制/反馈/重试局部反馈及滚动意图修正均已落地。最终前端 150/150、类型检查和生产构建通过；MVP、产品体验路径、DSH M6 SSE/滚动 56/56、反馈重试 20/20、反馈键盘与焦点 32/32，以及 Chrome/Edge/Firefox/WebKit 核心交互矩阵通过。浏览器使用确定性 API fixture，不代表真机软键盘或真实模型质量；虚拟列表和请求缓存按性能证据条件暂缓。
