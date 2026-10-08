# 前端交互与流畅度优化开发方案

> 适用项目：企业内部知识助手（Vue 3 + TypeScript + Vite）
>
> 参考对象：QuantumNous/new-api 的前端交互方式
>
> 文档目的：在不迁移 React、不破坏现有 SSE、引用、滚动、反馈和无障碍能力的前提下，改进本项目的局部渲染、操作反馈、加载状态和代码结构。

## 1. 结论与范围

New API 的流畅感主要来自状态更新范围、请求时机、局部反馈、滚动行为和移动端布局，而不是 React 本身。本项目继续使用 Vue 3，不引入 React，也不为了模仿 New API 引入一套完整的动画或 UI 框架。

本次方案确认做以下四类改动：

1. 限制流式消息的渲染范围和 Markdown 解析频率。
2. 拆分 `ChatView.vue` 与 `useWorkspace.ts` 中职责过重的逻辑。
3. 统一按钮、加载态、抽屉和操作结果反馈。
4. 增加可比较的前端性能测量，依据数据决定是否启用长会话虚拟列表。

本次明确暂缓以下改动：

- 不迁移到 React。
- 不直接复制 new-api 的源码、样式或资源。
- 不立即引入虚拟列表。
- 不立即引入 Motion、Framer Motion 或新的大型 UI 框架。
- 不重写当前 SSE 协议、`streamState`、滚动锚点和引用安全逻辑。
- 不为了视觉效果给所有元素增加动画或毛玻璃。

## 2. 当前基线与已具备能力

当前前端已经具备以下能力，开发时必须保留：

- `frontend/src/services/api.ts`：SSE 解码、中断、错误传播。
- `frontend/src/utils/streamEvent.ts`：事件字段和枚举校验。
- `frontend/src/utils/streamState.ts`：preview/result/done 状态边界、迟到事件隔离。
- `frontend/src/composables/useWorkspace.ts`：会话状态、AbortController、generation 隔离、草稿和会话恢复。
- `frontend/src/views/ChatView.vue`：阅读锚点、自动跟随、主动向上阅读、引用面板开合后的滚动恢复。
- `frontend/src/utils/markdown.ts`：Marked + DOMPurify + 绑定回答的引用按钮。
- `frontend/src/styles/tokens.css`、`base.css`、`chat.css`：设计变量、焦点样式、移动端滚动和聊天布局。
- 现有前端单测、生产构建和 Chrome/Edge/Firefox/WebKit 的交互验收。

当前主要问题不是功能缺失，而是实现集中度过高：

- `ChatView.vue` 约 900 行，混合消息渲染、滚动、输入、引用、反馈和导航。
- `useWorkspace.ts` 约 750 行，混合会话、聊天、知识库、上下文统计、上传和 Toast 状态。
- 消息模板直接调用 `renderMarkdown()`，流式更新时可能重复解析历史消息。
- 旧重试消息使用 `v-show` 隐藏，仍然保留在 DOM 中。
- 滚动过程中多次查询消息节点，长会话时成本会增长。

## 3. 目标与非目标

### 3.1 目标

- 流式生成时只更新最后一条助手消息及必要的滚动状态。
- 已完成消息的 Markdown HTML 不因新 token 到达而重复解析。
- 复制、反馈、上传、保存、重试、停止等操作在原位置有明确状态反馈。
- 组件职责清晰，后续修改输入框、引用面板或消息样式时不容易破坏滚动逻辑。
- 保持现有协议安全性、错误语义、焦点恢复和测试通过。
- 有 100、300、1000 轮会话的可比较性能数据。

### 3.2 非目标

- 不改变后端 API 和 SSE 事件格式。
- 不改变引用编号和证据绑定规则。
- 不改变用户已有的 Enter 换行、Ctrl/Cmd+Enter 发送行为。
- 不重做整体视觉风格，不照搬 New API 的后台布局。
- 不承诺没有测量证据的性能百分比提升。

## 4. 最终架构建议

### 4.1 聊天页面组件边界

将 `ChatView.vue` 逐步拆成以下组件，保持现有页面行为不变：

```text
ChatView.vue
├── ConversationViewport.vue
│   └── MessageList.vue
│       └── MessageItem.vue
│           ├── UserMessage.vue
│           ├── AssistantMessage.vue
│           ├── AnswerPreview.vue
│           └── MessageActions.vue
├── Composer.vue
├── ConversationNavigator.vue
├── EvidencePanel.vue
└── FeedbackDialog.vue
```

第一阶段不要求一次性完成所有拆分。优先抽出 `MessageItem.vue`、`AnswerPreview.vue` 和 `useConversationScroll.ts`，确认测试稳定后再拆 Composer。

### 4.2 状态边界

保留当前模块级 composable 的行为，先按领域抽出函数和类型，不急于引入 Pinia：

```text
useWorkspace.ts（兼容层）
├── session state：会话列表、当前会话、历史加载
├── chat state：消息、发送、停止、重试、流式状态
├── knowledge state：知识记录和上传
├── context usage：上下文统计
└── notification state：Toast
```

`useWorkspace()` 继续对现有调用方提供相同字段，内部可以逐步委托给领域模块。这样不会因为重构同时修改所有页面。

## 5. 详细开发任务

### 5.1 P0：流式 Markdown 与局部渲染

### 5.1.1 禁止在大模板中重复解析所有消息

将 `ChatView.vue` 中的直接调用：

```vue
v-html="renderMarkdown(message.content, ...)"
```

迁移到 `MessageItem.vue` 或 `AssistantMessage.vue` 的 computed 中。

要求：

- 每个消息只根据自身 `content`、`previewContent`、引用 ID 变化重新计算。
- 已完成且内容未变化的消息复用缓存 HTML。
- 引用 ID 改变时必须使缓存失效。
- 仍然经过 `DOMPurify`，不得为了性能绕过清洗。
- 不缓存未经清洗的 HTML。

推荐增加纯函数缓存层，而不是使用全局无限 Map：

```ts
type RenderedMarkdown = {
  content: string
  citationKey: string
  html: string
}
```

缓存应有上限或只存在于消息组件生命周期内，防止长会话无限占用内存。

### 5.1.2 流式预览节流

当前流式事件进入 reducer 的正确性保持不变，只在 UI 渲染层增加节流：

```text
SSE token
→ streamState reducer 合并文本
→ pending preview buffer
→ 50~100ms 内最多刷新一次 preview HTML
→ result 到达后完整解析正式答案
```

要求：

- `result` 仍然是正式答案唯一来源。
- `done` 没有 result 时仍不能伪造完成。
- 取消、错误、切换会话时取消定时器。
- 旧 generation 的 preview 不能覆盖新 generation。
- 调度使用 `setTimeout` 或 `requestAnimationFrame`，不得创建未清理的 interval。

第一版默认 50ms；只有性能测量表明 Markdown 仍然占用明显主线程时间时，才调整为 80~100ms。

### 5.1.3 局部更新验证

在开发模式性能工具中记录：

- 流式期间 `MessageItem` 更新次数。
- 非最后一条消息的更新次数。
- Markdown 解析耗时。
- 每次刷新产生的长任务。

验收要求：生成最后一条消息时，历史完成消息不能随每个 token 重新执行渲染逻辑。

### 5.2 P0：组件拆分与滚动逻辑隔离

### 5.2.1 抽出 `MessageItem.vue`

组件只接收消息和必要回调：

```ts
interface Props {
  message: UiMessage
  collapsed: boolean
  attemptSummary?: AttemptSummary
  expanded: boolean
}
```

消息组件负责：

- 用户问题或助手回答的展示。
- Markdown 和引用按钮。
- 过程摘要。
- 取消、错误、回退、重试状态。
- 复制和反馈入口。

消息组件不负责：

- 会话列表。
- 全局滚动位置。
- 发送请求。
- 打开或关闭整个页面的抽屉。

### 5.2.2 抽出 `useConversationScroll.ts`

保留当前行为，统一导出：

```ts
{
  nearBottom,
  navigationPaused,
  trackScroll,
  scrollToLatest,
  rememberReadingAnchor,
  restoreScroll,
  pauseFollowing,
}
```

实现要求：

- `scroll` 事件通过 `requestAnimationFrame` 合并。
- `ResizeObserver` 回调不能直接执行多次同步布局计算。
- 所有 observer、timer、animation frame 在卸载时清理。
- 引用面板开合后继续保持当前可见内容，误差小于现有验收阈值。
- 不改变 End、PageUp、触摸向上阅读和回到底部的现有语义。

### 5.2.3 降低全量 DOM 查询

不要在每次滚动时反复对整个容器执行多个 `querySelectorAll`。优先方案：

1. 给消息节点建立 `Map<messageId, HTMLElement>`。
2. 仅在消息列表变化时更新 Map。
3. 滚动时使用缓存节点计算当前导航项。
4. 如果仍然频繁，使用 `IntersectionObserver` 维护可见用户问题集合。

### 5.3 P0：按钮和操作反馈统一

### 5.3.1 统一状态模型

对按钮类操作使用以下状态，而不是每个页面自定义命名：

```ts
type ActionState = 'idle' | 'loading' | 'success' | 'error'
```

并非所有按钮都需要四种状态：

- 发送：idle/loading/error。
- 停止：loading/idle。
- 复制：idle/success/error。
- 反馈提交：idle/loading/success/error。
- 上传：idle/loading/success/error。
- 重试：idle/loading/error。

### 5.3.2 反馈规则

- 反馈在操作原位置显示，Toast 只作为补充。
- loading 时禁用重复提交，但停止按钮必须保持可用。
- success 状态显示 1~2 秒后恢复 idle。
- error 状态保留重试入口，不自动再次发送生成请求。
- 复制成功使用文字或图标切换，不使用大面积弹窗。
- 失败文本必须说明下一步动作，例如“重新生成”或“重试加载”。

### 5.3.3 基础按钮样式

沿用 `tokens.css` 的变量，统一 transition：

- 颜色、背景、边框：约 140~180ms。
- active：最多 `translateY(1px)`。
- 不给所有按钮增加 scale 动画。
- `focus-visible` 保持键盘可见。
- pointer modality 下不隐藏真正的键盘焦点。
- 尊重 `prefers-reduced-motion`。

建议在现有基础样式中抽出通用类或组件，不引入新的按钮库。

### 5.4 P1：加载态、抽屉和移动端

### 5.4.1 加载态

优先使用局部 Skeleton 或保留结构的 loading：

- 会话列表：保留列表容器和工具栏。
- 知识列表：保留表头、筛选和分页位置。
- PDF：保留标题、工具栏和预览区域。
- 上下文统计：保留卡片，仅替换数值区域。

不要用全屏 spinner 覆盖整个工作台，除非应用初始化确实没有任何可展示内容。

### 5.4.2 抽屉和弹层

统一检查：

- Escape 关闭最顶层弹层。
- 关闭后焦点回到触发按钮。
- 打开期间背景不可交互。
- 内部滚动使用 `overscroll-behavior: contain`。
- 手机端不被软键盘和安全区域遮挡。
- 动画在 150~250ms 内完成。

保留当前已有的 `Teleport`、焦点管理和弹层栈实现，不再引入第二套 Drawer/Dialog 系统。

### 5.4.3 响应式断点

当前 JS 和 CSS 存在各自断点。应集中定义一份断点契约，至少统一 760/768 附近的判断。

优先使用 CSS 控制布局；只有交互行为确实不同才使用 `matchMedia`。窗口变化监听必须清理。

### 5.5 P1：状态模块化与请求缓存

先不引入 Pinia 或 TanStack Query。将 `useWorkspace.ts` 内部拆成可独立测试的模块：

- `sessionState.ts`
- `chatState.ts`
- `knowledgeState.ts`
- `contextUsageState.ts`
- `notificationState.ts`

`useWorkspace.ts` 作为兼容门面继续返回旧 API。

只有在以下问题被性能或功能明确证明后，才引入请求缓存库：

- 多个组件重复请求同一列表。
- 页面切换反复闪烁。
- 需要跨页面失效和预取。
- 手动 generation/loading/error 代码继续显著增加。

如果需要缓存，先实现带 TTL、请求复用和失效能力的轻量层，不要同时引入 Pinia、TanStack Query 和另一套自定义缓存。

### 5.6 P2：长会话优化

先完成性能基线，再决定是否虚拟列表。

### 触发条件

在 300 轮或 1000 条消息样本中出现以下任一情况时再启用窗口化：

- 滚动帧率持续低于 50fps。
- 单次滚动出现超过 50ms 的长任务。
- 消息列表 DOM 节点数量明显影响交互。
- 首次打开历史或切换会话超过可接受阈值。

### 引入规则

- 流式生成的最后一条消息始终真实挂载。
- 变量高度消息必须保留测量和锚点恢复。
- 重试展开/折叠状态按 message ID 保存。
- 虚拟列表不能破坏引用焦点和导航。

在达到触发条件之前，只做两项低风险优化：

1. 隐藏的旧尝试改为按需渲染，不使用无限增长的 `v-show` DOM。
2. 历史消息的 Markdown 结果缓存。

## 6. 性能观测方案

在 `frontend/src/utils/performance.ts` 现有能力上增加开发模式指标，不记录问题文本、答案内容或来源内容。

建议记录：

```ts
{
  firstTokenMs,
  firstVisibleAnswerMs,
  streamUpdateCount,
  markdownRenderCount,
  markdownRenderMs,
  scrollLongTaskCount,
  messageCount,
  visibleMessageCount,
}
```

测试样本固定为：

- 2 轮：正常短会话。
- 20 轮：常规使用。
- 100 轮：当前验收规模。
- 300 轮：长会话压力。
- 1000 条消息：极端 DOM 压力。

每组至少记录：首次渲染、切换会话、流式生成、向上阅读、回到底部和打开引用面板。

不要在没有 before 数据的情况下声称“提升了百分之多少”。

## 7. 测试与验收门禁

每个阶段完成后执行：

```powershell
npm --prefix frontend test
npm --prefix frontend run build
```

涉及真实浏览器交互时执行现有相关脚本，至少覆盖：

- SSE 中文、emoji、CRLF、多行 data、心跳。
- 结果前断流、结果后断流、取消、重试、迟到事件。
- 生成中向上阅读、回到底部、End、输入框增高和缩小。
- 引用面板开合后的阅读位置和焦点恢复。
- 复制、反馈、上传、保存、重试按钮的 loading/success/error 状态。
- 320px、390px、1440px 视口。
- `prefers-reduced-motion`。
- 键盘 Tab、Shift+Tab、Escape 和输入法组合输入。

新增或修改组件时，必须增加至少一项针对真实行为的测试，不能只测试 class 是否存在。

## 8. 开发顺序

### 阶段一：建立基线

1. 保留当前代码不改，记录 2/20/100/300 轮样本。
2. 添加 Markdown、消息更新和滚动性能指标。
3. 确认当前构建产物和浏览器测试基线。

### 阶段二：消息局部更新

1. 抽出 `MessageItem.vue`。
2. 缓存已完成消息的 Markdown HTML。
3. 增加流式 preview 节流。
4. 修复取消、重试、切换会话时的 timer 清理。
5. 运行完整前端和 SSE 回归。

### 阶段三：滚动和结构拆分

1. 抽出 `useConversationScroll.ts`。
2. 引入 RAF 合并和消息节点缓存。
3. 把 `ChatView.vue` 的引用、反馈和输入逻辑逐步下沉。
4. 运行滚动导航和多浏览器验收。

### 阶段四：统一反馈

1. 统一按钮 action state。
2. 优化复制、反馈、上传、保存、重试的就地状态。
3. 统一 Skeleton、Drawer、Dialog 过渡。
4. 保持无障碍和 reduced-motion 行为。

### 阶段五：数据驱动的规模优化

1. 对比优化前后指标。
2. 只有达到触发条件时才实现虚拟列表。
3. 只有重复请求明显时才引入统一请求缓存。

## 9. 防止冗余建设的规则

开发过程中必须遵守：

1. 一个问题只保留一套实现。例如滚动跟随不能同时维护旧逻辑、第三方 stick-to-bottom 和新 composable 三套系统。
2. 不同时引入 Pinia、TanStack Query、自定义 cache 来解决同一类状态问题。
3. 不为简单的颜色、位移和淡入动画引入大型动画库。
4. 不新增全局状态保存已经可以由 `MessageItem` computed 得出的 HTML。
5. 不把业务状态复制到组件本地后再与 `useWorkspace` 双向同步。
6. 不为了虚拟列表提前改写当前稳定的滚动锚点，先用数据证明必要性。
7. 不以视觉动画掩盖慢请求；请求预取、缓存和局部 loading 优先于装饰动画。
8. 每一次抽象都必须减少重复调用、缩小更新范围或改善测试隔离；否则保留现有实现。

## 10. 完成标准

实现完成后，必须同时满足：

- Vue 项目仍可正常构建。
- 现有 SSE、引用安全、取消、重试和滚动测试全部通过。
- 生成过程中历史消息不会被重复 Markdown 解析。
- 生成中的非最后一条消息不会发生无关更新。
- 所有操作按钮在 loading、成功和失败时都有明确且不冲突的反馈。
- 键盘、触摸、移动端软键盘和 reduced-motion 行为没有回归。
- 没有并行存在两套 Drawer、按钮状态、滚动跟随或请求缓存方案。
- 是否引入虚拟列表和请求缓存有性能数据作为依据，而不是凭感觉决定。

## 11. 2026-10-08 实施状态

本方案的代码改造范围已完成：

- 消息局部渲染、Markdown 缓存、流式预览节流、`MessageItem.vue`、`AnswerPreview.vue` 和 `useConversationScroll.ts` 已交付。
- `Composer.vue` 已从 `ChatView.vue` 拆出，输入法、快捷键、自动增高、模式切换、停止和示例问题保持原有行为。
- `useWorkspace.ts` 已按 session/chat/knowledge/context 领域拆出状态工厂，保留原兼容门面；通知状态继续由独立模块管理。
- 上下文统计与知识库加载/上传动作已进一步移到 `contextActions.ts`、`knowledgeActions.ts`；`useWorkspace.ts` 只保留兼容编排和聊天/SSE 逻辑。
- 复制、反馈、上传、重试、停止等局部反馈已统一；重试按钮增加就地 loading/success/error 状态。
- 会话列表和知识库列表增加保留结构的 Skeleton，并尊重 `prefers-reduced-motion`。
- 性能指标与 2/20/100/300 轮、1000 条消息的合成基线已记录；简单窗口化原型因滚动帧率下降而删除。

2026-10-08 最新验证：前端 148 项测试、类型检查和生产构建通过；DSH SSE/滚动专项 56/56；Chrome、Edge、Firefox、WebKit 的交互矩阵中，输入法、长会话、复制、反馈、抽屉、失败重试和低高度布局场景均通过。浏览器验证使用确定性 API fixture，不代表真机软键盘或真实模型质量。

以下事项仍属于外部验收或条件性增强，不标记为代码已完成：真机触屏/软键盘、同配置 before/after 性能收益、真实授权企业资料与独立业务质量、虚拟列表（需再次证明当前 DOM 触发阈值且采用变量高度锚点方案）、请求缓存（需出现重复请求证据）。

