# DSH 参考前端改造开发方案

更新日期：2026-10-07

适用项目：企业内部知识助手（当前 Vue 3 / TypeScript / FastAPI / LangGraph 项目）

文档性质：可执行开发方案，保留原设计、开发步骤和历史交付记录。当前状态以 §1 阶段表和最新验收记录为准；拟新增文件与设计示例不自动代表当前文件结构，实际分工和差异见 §13。明确暂缓的增强项不列为本阶段漏做。

## 1. 目标与开发顺序

目标：在保留知识引用、回答校验、取消、重试与会话隔离的前提下，让输入、等待、阅读、核对来源的体验更清晰。

按下列顺序逐模块开发；每完成一项，先通过该项的测试和验收，再开始下一项。一次提交尽量只解决一个完整用户场景。

| 阶段 | 工作 | 当前状态 | 完成门禁 |
|---|---|---|---|
| M0 | DSH 风格输入卡片与问答排版 | 已完成本轮样式调整 | 保留现有截图和构建结果，后续修改不得破坏 |
| M1 | 会话上下文预算圆环 | 已完成 | 数值口径正确，刷新、切换、压缩、取消后可恢复 |
| M2 | 输入框交互与已完成过程折叠 | 已完成 | 中文输入不误发，每轮过程独立且能恢复 |
| M3 | Markdown 安全与长文本稳定性维护 | 轻量维护回归已通过；代码块增强暂缓 | 最终回答替换正确，引用和 HTML 清洗无退化，长文本不撑破布局 |
| P1 | 引用来源原文预览与定位 | 文本定位及四浏览器 PDF 专项已完成，2026-10-07 | 定位可解释、版本不混淆、权限与降级正确 |
| M4 | 回答反馈闭环 | 已完成；2026-10-07 补齐失败重试幂等及专用键盘/焦点验收 | 反馈关联真实回答、权限校验、重试不重复、键盘与焦点正确 |
| M5 | 长会话问题导航 | 已完成，2026-10-06 验收通过 | 跳转准确、重试组不重复、返回位置稳定 |
| M6 | SSE 与滚动专项加固、综合验收 | 专项已完成，2026-10-07；真机验收另列 | 断流、取消、重试和阅读位置均通过回归 |

执行顺序：M0 → M1 → M2 → M3 → P1 → M4 → M5 → M6。P1 为新增来源预览阶段，保留原 M 编号便于追踪；先交付 P1-A 文本定位，再交付 P1-B PDF 原页预览。

M6 的基础不变量从 M1 起持续验证，不应等到最后才处理明显的流式错误。上下文完整模型容量展示、附件问答、复杂富文本编辑器不纳入第一阶段。

## 2. 方案起始代码基线与边界（2026-10-02）

本节保留方案编制时的代码基线；后续模块的实际完成状态见阶段表、§13 交付记录和 [当前状态](current_status.md)。本节的起始接口描述不作为当前能力缺失清单。

### 2.1 已完成的 M0

本轮已修改：

- `frontend/src/views/ChatView.vue`：模式切换移到输入卡片底部；圆形发送/停止按钮；快捷键和来源提示移到卡片外；欢迎文案。
- `frontend/src/styles/chat.css`：浅灰输入卡片、大圆角、问答间距、浅灰用户气泡、移动端工具栏换行。

当轮验证记录：前端 92 项测试通过；生产构建通过；Edge 模拟 1440×900、390×844、320×640、844×390 的发送和引用交互通过；通用模式、联网开关、快捷键发送和停止通过。浏览器使用模拟 API，不是真实模型验证；不能推导其他浏览器或真机软键盘已通过本次改造。

截图：`work/dsh-composer/empty-1440.png`、`work/dsh-composer/conversation-390.png`、`work/dsh-composer/general-320.png`。这些属于本地验证产物，不要求提交 Git。

### 2.2 需要复用的现有能力

| 文件 | 当前职责 | 改造原则 |
|---|---|---|
| `backend/agent/conversation.py` | token 估算、历史压缩计划 | 复用估算函数，不在前端另外实现一套计数 |
| `backend/agent/nodes.py` | `manage_conversation_context`、生成与校验 | 新增统计不额外调用模型，不改变校验顺序 |
| `backend/config.py` | 会话预算、摘要阈值及请求预算 | 读取有效配置，不把默认值硬编码进 UI |
| `backend/agent/memory.py` | SQLite 会话、消息和最终轮次结果 | 需要恢复的数据在这里补持久化与迁移 |
| `backend/agent/sessions.py` | `thread_id_for`、进程内会话锁、删除 | 使用统一会话身份；不能自行拼装另一套 ID |
| `backend/agent/checkpoint.py` | 过滤暂存候选内容的 checkpoint | 不让未校验草稿进入正式历史 |
| `backend/api/routes.py` | SSE、历史读取、幂等结果重放 | 同时覆盖正常结果、重放、异常和历史恢复 |
| `frontend/src/services/api.ts` | fetch 流式解析和 REST 请求 | 保留现有 API 基址和中断机制 |
| `frontend/src/utils/streamState.ts` | 流式事件到 UI 状态的 reducer | `result` 为正式答案，`done` 不是答案确认 |
| `frontend/src/composables/useWorkspace.ts` | 会话状态、草稿、发送与取消 | 防止旧会话异步结果污染当前会话 |
| `frontend/src/components/AgentProgress.vue` | 执行步骤与当前耗时 | 扩展而非另建相互竞争的进度面板 |
| `frontend/src/utils/markdown.ts` | marked、DOMPurify、引用按钮 | 保留清洗和只允许已确认引用的约束 |
| `frontend/src/components/EvidencePanel.vue` | 引用选择、来源详情入口、异步结果隔离 | 保留回答和引用绑定，增强原文定位 |
| `frontend/src/components/DocumentDetailDrawer.vue` | 当前文档元数据、文本预览、下载 | 复用外壳，不再增加第三层抽屉 |
| `frontend/src/composables/useEvidenceSelection.ts` | session/message/citation 快照与焦点入口 | 扩展定位目标，不能仅靠显示编号 C1 |
| `frontend/src/styles/tokens.css` | 颜色、间距、圆角、动效 | 新组件沿用现有设计变量 |

当前默认配置：会话预算 12,000；摘要触发阈值 10,000；摘要目标 1,500；近期保留参数 4 轮。必要时保留轮数会缩减；没有可压缩旧消息等情况也可能无法压缩。这不是“永远不超过 12,000”的硬保证，更不是模型完整上下文容量。运行环境可覆盖默认配置。

`budget_snapshot.input_tokens/output_tokens` 表示本次请求的调用消耗，不是当前会话占用。方案起始历史接口返回 `{items: [...]}`，前端 `fetchHistory` 只提取 items；当时尚无这里设计的 `context_usage` 和反馈接口。M1/M2/M4 开发后已提供上下文读取、正式结果中的统计、历史过程摘要与反馈提交能力。

## 3. DSH 参考范围

本次核查固定源码版本：`639ed015397290b3745d163aafe02ffee4aa3f84`。以后参考升级版本需重读相关源码，不能沿用本表当作最新版保证。

| 参考点 | 固定版本源码 |
|---|---|
| 输入卡片层次 | [InputBar.module.css](https://github.com/deepseek-ai/deepseek-harness/blob/639ed015397290b3745d163aafe02ffee4aa3f84/packages/client/ui-conversation/src/client/skeleton/InputBar.module.css) |
| 圆环、点击详情 | [ContextMeter.tsx](https://github.com/deepseek-ai/deepseek-harness/blob/639ed015397290b3745d163aafe02ffee4aa3f84/packages/client/ui-conversation/src/client/skeleton/ContextMeter.tsx) |
| 占用比例计算 | [context-occupancy.ts](https://github.com/deepseek-ai/deepseek-harness/blob/639ed015397290b3745d163aafe02ffee4aa3f84/packages/client/ui-conversation/src/client/context-occupancy.ts) |
| 过程折叠 | [TurnProcessNodeView.tsx](https://github.com/deepseek-ai/deepseek-harness/blob/639ed015397290b3745d163aafe02ffee4aa3f84/packages/client/ui-chat/src/client/chat/TurnProcessNodeView.tsx) |
| 输入法与按键仲裁 | [keymap.ts](https://github.com/deepseek-ai/deepseek-harness/blob/639ed015397290b3745d163aafe02ffee4aa3f84/packages/client/ui-conversation/src/client/input/editor/keymap.ts) |
| 增量 Markdown | [incremental.ts](https://github.com/deepseek-ai/deepseek-harness/blob/639ed015397290b3745d163aafe02ffee4aa3f84/packages/client/ui-primitives/src/markdown/incremental.ts) |
| 代码块 | [CodeBlock.tsx](https://github.com/deepseek-ai/deepseek-harness/blob/639ed015397290b3745d163aafe02ffee4aa3f84/packages/client/ui-primitives/src/markdown/CodeBlock.tsx) |
| 反馈 | [FeedbackDialog.tsx](https://github.com/deepseek-ai/deepseek-harness/blob/639ed015397290b3745d163aafe02ffee4aa3f84/packages/client/ui-message-feedback/src/client/FeedbackDialog.tsx) |
| 问题导航 | [TurnNavigator.tsx](https://github.com/deepseek-ai/deepseek-harness/blob/639ed015397290b3745d163aafe02ffee4aa3f84/packages/client/ui-chat/src/client/chat/TurnNavigator.tsx) |
| 滚动跟随 | [use-scroll-follow.ts](https://github.com/deepseek-ai/deepseek-harness/blob/639ed015397290b3745d163aafe02ffee4aa3f84/packages/client/ui-chat/src/client/chat/use-scroll-follow.ts) |
| 浏览器流传输 | [stream-client.ts](https://github.com/deepseek-ai/deepseek-harness/blob/639ed015397290b3745d163aafe02ffee4aa3f84/packages/api/gateway/src/client/stream-client.ts) |
| 模型侧 SSE | [sse.ts](https://github.com/deepseek-ai/deepseek-harness/blob/639ed015397290b3745d163aafe02ffee4aa3f84/packages/llm/llm-deepseek/src/sse.ts) |

DSH 浏览器通信使用 WebSocket 多路复用；模型适配层有 SSE。我们保留 fetch + SSE，借鉴状态边界与交互，不迁移整个 Cordis/React 架构。MIT 允许复用；复制实质代码时保留对应版权及许可证，第三方依赖另核对其许可。M0 为按交互思路改造现有 Vue 页面，不是复制 React 组件。

## 4. M1：会话上下文预算圆环

### 4.1 产品口径

标题固定为“会话上下文预算”，显示“估算 X / Y tokens”和百分比。说明：仅统计 checkpoint 实际保留的消息与摘要，不含系统提示、检索材料、工具定义或尚未发送的草稿。摘要占用包括在总数内，不重复相加。

- 分子：`estimate_conversation_tokens(messages, conversation_summary)`。
- 分母：有效 `settings.conversation_token_budget`。
- 超过阈值表示下次执行上下文管理时会尝试压缩，不承诺立即压缩或必然成功。
- 圆弧夹到 0–100%；文本允许显示超过 100% 的实际估算比例，并提示超出预算。不要把超限文本也截成 100%。
- 估算仅表示会话历史管理，不显示“模型还剩多少 token”；没有数据不能显示 0%。
- 空会话：确认没有 checkpoint 后返回已知的 0；读取失败返回不可用。两者必须区分。

### 4.2 会话上下文统计契约（已实现）

`frontend/src/types/api.ts` 已定义下面的类型，后端提供等价响应 schema；JSON 使用 snake_case：

```ts
interface ContextUsage {
  scope: 'conversation_history'
  estimation_method: 'mixed_text_v1'
  estimated: true
  status: 'available' | 'unavailable'
  used_tokens: number | null
  summary_tokens: number | null
  budget_tokens: number
  summary_trigger_tokens: number
  sampled_at: string // UTC ISO 8601，表示采样时间，不作为并发版本号
  checkpoint_id: string | null
}
```

available 时计数为非负整数，budget > 0；unavailable 时 used/summary 为 null。不得输出 NaN/Infinity。示例中的时间和用量应在实现时动态生成。

新增 `GET /chat/context/{user_id}/{session_id}`，响应 `{session_id, context_usage}`。前端经现有 API_BASE 访问；默认浏览器路径为 `/api/chat/context/...`，后端路由不要重复加 `/api`。

选择独立读取接口，第一版不改历史列表的返回类型。后端取 `request.app.state.graph`，通过 `thread_id_for` 构造配置，使用 `graph.aget_state` 读取当前保存状态；先验证用户路径与已认证身份一致。读取时不得触发摘要、生成或历史写入。

### 4.3 开发步骤

1. 在 `backend/agent/conversation.py` 增加纯统计函数（建议名 `build_context_usage`），集中计算消息和摘要用量；只有 API 层负责时间和 checkpoint 标识。复用当前估算器，避免以字符长度冒充精确 tokenizer。
2. 在 `backend/api/routes.py` 实现上述 GET。按已有会话锁协调读取、生成和删除；锁是进程内的，不声称跨 worker 一致性。抽出不自行加锁的内部读取函数：独立 GET 在外层加锁，已经位于 `_stream_graph_unlocked` 会话锁内的 result 组装直接调用内部函数，严禁重复获取非重入锁。独立 GET 的锁等待/读取设置有限超时（建议 2 秒，可配置），超时返回 unavailable，前端保留旧样本并标记“数据暂未更新”；仅请求进行中显示“更新中”，失败后不能永久转圈。客户端取消读取不应影响正在生成的请求。
3. 最终 `result` 事件增加可选 `context_usage` 字段。应读取已提交 checkpoint，而非候选回答或局部节点输出；覆盖正常、业务回退、幂等重放和运行异常路径。锁内统计读取也需有限超时（建议 2 秒）；读不到时返回 unavailable，统计失败不得使已成功答案失败。
4. 重放旧轮次时，答案仍重放该轮结果，圆环采样当前会话状态，不能把旧轮 token 快照伪装为当前占用；字段的采样时间明确这一点。不将该字段混进请求成本预算。
5. `useWorkspace.ts` 按 session 缓存占用；进入会话、刷新、删除后新建、请求取消/断线后，通过 GET 重新采样。发生并发时用本地请求 generation 序号丢弃旧响应；每次发送使此前读取失效，结束后再刷新，不能仅靠时间戳比较。
6. 活跃生成期间保留上次样本并标注“更新中”，不要按预览文字长度伪造实时进度；请求结束采样后撤销标记。取消后服务端可能仍在收尾，先显示最近保存状态，后续进入会话时再读；不要无限轮询。
7. 修改 `types/api.ts`、`streamState.ts` 或独立事件分发层接收新增字段。若用 reducer 处理，所有状态构造与测试工厂一并补齐。GET 与 result 共用同一格式。
8. 新建 `frontend/src/components/ContextMeter.vue`（拟新增），仅负责展示，接收数据与 loading/stale 状态，不直接发请求。放到 `composer-meta` 左侧，移动端仍保留；用 SVG 圆环，避免新增图表依赖。
9. 悬停/键盘聚焦显示简短说明，点击固定详情；Esc/外部点击关闭。点击固定后移出触发器不能立即关闭。手机可点，触发区域至少 44×44；面板左右保留 12px、窄屏不溢出。未知数据用“用量暂不可用”，禁用误导性进度值。

### 4.4 测试与验收

扩展 `tests/test_conversation_context.py` 和 `tests/test_api_streaming.py`；必要时拟新增 `tests/test_context_usage.py`。前端拟新增 `ContextMeter.test.ts`，同时扩展 workspace/API 测试。

必测：空会话 0；中英文估算；摘要不重复计数；压缩后下降；没有可压缩消息时维持真实超限；预算环境覆盖；无权读取；状态服务不可用；正常/回退/异常/旧轮重放；会话 A 慢响应不覆盖 B；刷新恢复；取消后重新采样；面板键盘和触控。

门禁：同一个 checkpoint 的接口分子等于后端估算函数结果；前端没有硬编码 12,000；浏览器历史消息很多但 checkpoint 已压缩时，显示压缩后用量；任何统计错误都不阻断正式回答。

## 5. M2：输入框交互与过程折叠

### 5.1 输入框

文件：`ChatView.vue`、`chat.css`；复杂时将输入逻辑抽为拟新增 `useComposerInput.ts`，不要在一次样式修改中替换成 Lexical。

1. 保持现有 Enter 换行、Ctrl/Cmd+Enter 发送，不悄悄改用户习惯。
2. 当前有 compositionstart/end 和 isComposing 防护；补按键 repeat 防重发，研究并验证 Safari compositionend 与结束按键的顺序，不机械复制固定延迟。
3. 支持内容自动增高：输入/草稿恢复后 nextTick 测量 scrollHeight；先重置高度以允许缩小，再限制到 `min(240px, 30dvh)`；超过上限内部滚动。空白输入回到基准高度，手机输入字号至少 16px。
4. 自动增高也要覆盖会话切换、示例问题填入、编辑原问题、发送清空、窗口缩放；保留草稿确认逻辑。
5. 忙碌时可编辑下一条草稿，但不可重复发送；停止按钮仍操作当前请求。只优化纯文本粘贴，不在这里引入附件上传业务。
6. 字符上限和 token 预算是两种约束：现有 12,000 字符提示继续明确写“字/字符”，不能改成 12,000 tokens。服务端预算校验失败时保留原问题或提供编辑入口。

验收：中文候选确认不发送；长按组合键不会重复提交；多行草稿增高/缩小正确；320px 和手机横屏工具栏可操作；有原草稿时编辑旧问题不会静默覆盖。

### 5.2 每轮执行过程

方案起始时 `AgentProgress.vue` 主要挂载在正在发送的回答上，尚无每轮步骤恢复。当前每条回答已保留过程摘要，服务端通过 metadata_json 持久化并支持历史恢复；旧记录没有过程信息时仍不伪造步骤或耗时。

已实现的过程摘要契约：

```ts
interface TurnProcessSummary {
  status: 'complete' | 'fallback' | 'error' | 'cancelled'
  elapsed_ms: number | null
  steps: Array<{ node: string; status: 'pending' | 'running' | 'complete' | 'skipped' | 'error' | 'cancelled'; duration_ms: number | null }>
}
```

1. 给 `UiMessage` 增加当前步骤快照；请求中每次 reducer 更新只写对应回答。不同轮次不共享一个会被覆盖的步骤数组。历史步骤耗时由服务端单调时钟和实际事件产生，客户端计时仅作实时展示，不回传为可信历史；无法取得的时长为 null。
2. 活跃时显示当前阶段；终态显示“已完成/已安全回退/未完成/已停止”，可展开实际步骤。错误与取消不能标成成功；总耗时从统一起点计算，不能盲目相加可能重叠的节点耗时。
3. 第一小步允许仅会话内保留，但交付 M2 前需补历史恢复。后端将白名单步骤摘要和耗时写入 `chat_turn_results` 已有的 `metadata_json`，新增其中的 `process_summary` 键；优先复用现有列，不为相同用途新增另一列。`_turn_metadata` 当前有字段白名单，需要同步扩展并校验嵌套结构；`persist_completed_turn`、`load_completed_turn`、历史组装、SSE result 均补字段。保留既有数据库初始化和迁移兼容逻辑。
4. `memory.py` 的 `load_chat_messages` 已按 user/session/turn LEFT JOIN 轮次表，沿用现有查询并扩展返回字段；旧记录没有字段就隐藏详情，不伪造历史耗时。数据库写入不要存 prompt、模型思维链或异常堆栈。
5. 取消若没有服务端终态记录，只保留本地“已停止”，刷新后允许没有过程详情；不为补 UI 恢复而制造正式回答记录。必要时后续单独设计 attempt 记录表。
6. 复用已有 `AgentProgress.vue`，增加终态/固定耗时输入；结束后清理 interval，展开状态按 message ID 隔离。重试组沿用 `utils/attempts.ts` 的语义。

测试：扩展 AgentProgress、streamState、useWorkspace 与 API 测试；补新/旧数据库迁移、重复 turn 重放、两轮过程互不覆盖、失败和取消标签、刷新旧历史。

## 6. M3：Markdown 安全与长文本稳定性维护

### 6.1 项目适配判断

本项目的核心知识库是企业制度、流程、合同、财务、人事和合规文档。当前种子资料和评测问题以自然语言制度问答为主，没有证据表明代码块是主要用户场景。现有 `frontend/src/utils/markdown.ts` 已使用 marked、DOMPurify 和按回答绑定的引用按钮，相关测试已覆盖恶意 HTML、引用清洗以及代码文本中的 `[C1]`。

因此，M3 不再以 DSH 的代码块能力为交付目标，也不默认引入增量解析、语法高亮或新的 Markdown 组件。代码块增强只有在真实知识库出现 SQL、脚本、配置文档，或用户反馈形成明确需求后再单独立项。

### 6.2 保留与维护范围

1. `preview_delta` 的 generation_id 改变时继续丢弃旧预览；`token` 仍是暂存内容；`result` 到达后全量解析正式答案并重新绑定确认引用。
2. 继续使用 DOMPurify 清洗用户输入和模型输出；引用按钮只能由当前回答已确认的 citation ID 生成，代码文本中的 `[C1]` 不得变成引用按钮。
3. 只做必要的长文本布局维护：表格、超长链接、长行文本和已有代码围栏在窄屏内部滚动，不撑破聊天页面；不把后端等待时间算作 Markdown 性能收益。
4. 若未来出现真实卡顿，先用 2k/10k/50k 字符样例测量解析次数、总渲染耗时和长任务，再决定是否需要有界缓存或块级增量解析。没有测量证据时不新增 `streamingMarkdown.ts`、`AnswerMarkdown.vue` 或高亮依赖。

### 6.3 M3 验收

验收保留中文段落、列表、表格、引用式链接、恶意 HTML/URL、跨分片 Markdown、字面代码中的 `[C1]` 和最终答案替换。通过现有 Markdown、SSE、构建和窄屏布局回归即可；不要求代码复制工具栏、语言高亮或代码块专项交付。

## 7. P1：引用来源原文预览与定位

### 7.1 起始不足与交付边界

现有 EvidencePanel 使用 fetchKnowledgeRecord 打开 DocumentDetailDrawer，后者已有当前文档正文、元数据和原文件下载。后端已有带 ACL 的 `GET /knowledge/records/{source_id}` 与 `/download`。当前详情明确声明不是历史版本快照，必须保留这一边界。

方案起始不足：详情入口没有传入所选引用作为定位目标，正文整体按文本展示，没有引用高亮、页/段导航或 PDF 原页渲染。这些功能现已实现，验证范围见 P1 验收记录；旧记录仍可能缺少 page/section，page 字段不等于已有 PDF 坐标。

- **P1-A 必做**：抽取文本预览、引用定位与前后文、长文分窗口加载、版本差异提示、权限及会话隔离。
- **P1-B 随后做**：原始 PDF 按页渲染、缩放与文本层查找；完成后才能宣称支持 PDF 原页预览。
- TXT/Markdown/JSON 展示纯文本或抽取文本；DOCX 第一版展示抽取段落和表格文本，不承诺 Word 原版式和页码复刻。
- 扫描 PDF 没有文本时提示“未提取到可定位文本”；P1-B 可展示图像页，但不宣称 OCR 或文字精确定位。
- 联网引用只提供经过 http/https 校验的外链，不任意抓取 citation.url，也不嵌入第三方网页。

DSH 固定版本参考：[TextPreview.tsx](https://github.com/deepseek-ai/deepseek-harness/blob/639ed015397290b3745d163aafe02ffee4aa3f84/packages/client/ui-sidebar-documentpreview/src/client/TextPreview.tsx)、[LazyPdfBody.tsx](https://github.com/deepseek-ai/deepseek-harness/blob/639ed015397290b3745d163aafe02ffee4aa3f84/packages/client/ui-sidebar-documentpreview/src/client/pdf/LazyPdfBody.tsx)。已读源码，借鉴分类型预览、加载/失败状态及 PDF 渲染器懒加载，不移植本地工作区文件访问协议。

### 7.2 文件范围

| 文件 | 具体改动 |
|---|---|
| `frontend/src/views/ChatView.vue` | 将 session、turn/message 和选中引用交给证据面板 |
| `frontend/src/composables/useEvidenceSelection.ts` | 补 turn_id 和稳定定位目标，保留引用快照与触发元素 |
| `frontend/src/components/EvidencePanel.vue` | “查看原文”传入完整目标；返回后恢复引用列表滚动和焦点 |
| `frontend/src/components/DocumentDetailDrawer.vue` | 接入可复用预览 body；知识管理入口无引用时仍可打开 |
| `frontend/src/services/api.ts`、`frontend/src/types/api.ts` | 新增预览 API、AbortSignal 与响应类型；Citation 补 version 等后端已有可选字段 |
| `backend/api/routes.py` | 增加预览接口，复用身份和来源 ACL，保持旧详情及下载兼容 |
| `backend/agent/memory.py` | 复用持久化引用、记录、segments 和读取 helper |
| `backend/agent/citations.py` | 新引用补可选 source_content_checksum，不改变 evidence_id 和显示编号语义 |
| `frontend/src/styles/chat.css`、`frontend/src/styles/tokens.css` | 阅读宽度、高亮、窗口导航、手机布局 |

当前新增 `backend/agent/source_preview.py`（纯定位及窗口构建）与 `frontend/src/components/PdfSourcePreview.vue`（懒加载原页）；文本预览请求、窗口和定位状态留在 `DocumentDetailDrawer.vue`。原拟议的 `useSourcePreview.ts`、`SourcePreviewBody.vue` 未单独拆出，不以拟议文件名判断功能完成；未重写整套抽屉系统。

### 7.3 来源预览接口与响应契约

新增 `GET /knowledge/records/{source_id}/preview`。首次定位请求携带 session_id、turn_id、citation_id，三者一起提供或全部省略。服务端从当前身份拥有的已完成回答读取引用，并核实 source_id 一致；不接受客户端自由提交 quote/chunk_id 来证明引用已验证。无定位参数时用于知识管理普通预览。

续读使用不透明 cursor，绑定当前身份、来源 ID、内容校验摘要、切块规则版本和定位目标。采用签名载荷或严格校验的服务端状态，不包含客户端可改写的磁盘路径。每次续读重新鉴权；内容版本变化返回 409，客户端清除旧块后重新加载，禁止拼接两个版本。

```ts
interface SourcePreviewResponse {
  source_id: string
  title: string
  version: string | null
  content_checksum: string
  format: 'text' | 'pdf' | 'docx' | 'other'
  render_mode: 'extracted_text'
  revision_match: 'same' | 'changed' | 'unknown'
  location: {
    status: 'exact' | 'normalized' | 'ambiguous' | 'not_found' | 'unavailable'
    block_id: string | null
    page: number | null // 原文件 1-based 页号，未知为 null
    section: string | null
    start: number | null
    end: number | null // 对 block.text 的 Unicode code point 半开区间
    match_count: number
  }
  blocks: Array<{ id: string; text: string; page: number | null; section: string | null }>
  previous_cursor: string | null
  next_cursor: string | null
  original_available: boolean
}
```

start/end 仅在 exact/normalized 且唯一定位时非 null，满足 `0 <= start < end <= codePointLength(text)`。前端用 Array.from(text) 按同一计数切片，不能直接 UTF-16 substring，否则 emoji 会使高亮错位。跨块引用第一版标 unavailable，不伪造单块范围；以后支持多块时显式升级 ranges 契约。

location 描述引用目标，翻到其他窗口时目标块可以不在 blocks 内；只有目标块已挂载才执行定位。回到引用位置重新发起首次定位请求。普通预览没有引用时 location.status=unavailable、match_count=0、其余定位字段为 null；ambiguous 返回实际匹配数量；not_found 的 match_count=0，不同时给出假坐标。旧记录缺当前 checksum 时在后端用现有正文校验算法补算响应值，不必在只读请求中回写知识库；不能把空摘要当作 same。

状态码：无权或不存在来源/所属回答统一 404；参数不完整或越界 422；cursor 对应版本变化 409；暂时失败 503。摘录未找到、多处匹配、页码缺失返回 200 和相应 location 状态。前端仍使用 API_BASE。

拟议可配置上限：每窗口最多 12 块、总计不超过 24,000 code points、每块最多 4,000；超长 segment 拆为稳定子块。首次以命中块为中心返回邻近窗口，未定位返回开头。窗口分页不等于 PDF 原始页码。

### 7.4 定位与版本算法

1. 先鉴权再加载记录，从已持久化回答引用取得 evidence_id、document_id、version、chunk_id、page/section、quote。引用与来源不匹配时终止，不按标题猜来源。
2. 从记录 segments 构建稳定文本块；旧记录缺 segments 时从正文构建，页码/章节为 null。读取不调用模型、不重新嵌入、不触发 OCR。
3. 新引用附带 source_content_checksum，取检索文档 metadata 已有的 content_checksum；同步后端类型、实际序列化字段、前端类型和 fixtures。旧历史不改写，缺 checksum 则 revision_match=unknown；同一 version 字符串不证明内容相同。
4. 引用 checksum 与当前记录 checksum 不同则 changed，显示“来源已更新，以下为当前版本”。evidence_id 能标识证据片段，但不能冒充整份文档的版本摘要。
5. 优先 page/section 缩小候选范围；chunk_id 只有在能验证内容时才作定位提示，不能用当前切块参数重新生成同序号来证明同一证据。第一版不重建向量索引。
6. quote 去首尾空白后精确匹配当前块，唯一命中为 exact；失败时允许折叠连续空白/换行，维护规范化字符到原 text code point 的映射，唯一命中为 normalized，界面说明“按文本匹配定位”。
7. 重复短句为 ambiguous，不静默选第一处；未命中为 not_found。截断、拼接、省略号、PDF 连字符差异允许定位失败，不把模糊相似结果说成精确原文。
8. 版本一致性与定位精度独立：当前版有同一句，不代表历史版本相同。区分“回答引用摘录”和“当前文档正文”；本阶段不提供完整历史文件版本快照。
9. 文件丢失但授权记录正文尚在时可看抽取文本，original_available=false；来源不可访问时不返回新正文或标题。下载可用性必须检查真实文件与路径边界，不能只看文件名非空。

### 7.5 前端实施步骤

1. 请求身份由 sessionId、messageId/turnId、citationId、sourceId 组成；C1 在不同回答可重复，不能作全局缓存 key。旧消息缺持久化 turn_id 时仅提供普通预览，并标明无法关联定位。
2. useSourcePreview 使用 AbortController 和 generation 防竞态；A→B、关闭、换会话时取消旧读取并清空旧正文，只有匹配 generation 的响应落状态。
3. 沿用证据面板→详情两层关系，详情内增加“原文 / 文档信息”切换，不再叠第三层。桌面可采用约 560px 阅读宽度并允许展开；手机占可用宽度，正文独立滚动。
4. 顶部显示标题、版本差异提示、存在的页/章节和“回到引用位置”。元数据与校验摘要放信息页。加载显示骨架，失败同位重试，旧正文不闪现。
5. 挂载目标窗口后 nextTick 滚到对应块，使用 Vue 文本节点与 mark 分片高亮，不拼接 quote 到 innerHTML。P1-A 默认纯文本 pre-wrap，避免 Markdown 渲染改变文本偏移。
6. ambiguous/not_found/unknown 给明确说明及手动浏览入口，不显示“已精确定位”。提供上一/下一窗口和回到引用处，不一次挂载全文。
7. 缓存建议最多 5 个 source/revision 组合，仅当前会话内存使用；打开时重新鉴权，401/403/404 清缓存。换身份/会话/卸载时清理，不把企业全文放 localStorage/IndexedDB。
8. 下载继续现有 API，文件不存在则禁用并解释，不影响文本预览。联网引用只给安全外链，使用 noopener/noreferrer。
9. 关闭详情回证据入口，关闭证据回回答引用；Esc 每次只关最上层。异步加载结束不抢走用户已移动的焦点；状态和定位结果可被屏幕阅读器读取，减少动画时直接跳转。

### 7.6 P1-B：PDF 原页预览

1. 先保证文本预览可用，点击“原页”才加载渲染器，建议 pdfjs-dist，实施时核对版本、worker 和打包兼容性。聊天首屏不静态加载 PDF 依赖。
2. 通过授权下载 API 获取 Blob/ArrayBuffer，保持后端 ACL 和路径边界。当前下载路由没有预览专用大小门禁，P1-B 需新增检查或单独的授权原页读取入口；建议预览上限 20 MiB、500 页并配置化。若新增入口，不限制用户原有授权下载能力。前端在完整分配前检查响应长度并在流式读取中累计字节，超限取消；交本地 worker，不传第三方在线预览服务。
3. 验证 citation.page 在原始文件范围内，每次只渲染当前页及必要邻页；限缩放、限并发。切页取消旧 renderTask，关闭销毁 document/loadingTask 和专属 worker，释放 Blob URL。
4. 提供页码、前后页、适应宽度、缩放和下载；建议缩放 50%–200%。文件页序不等于正文印刷页码。
5. 无坐标时只保证跳页；文本层只有唯一匹配且映射可信时才高亮。断行、双栏、旋转等无法映射时提示“已打开该页，未能精确标注”，不能把抽取文本偏移直接换成 canvas 坐标。
6. 扫描件可显示图像页。加密、损坏、解析失败、超限降级到抽取文本或授权下载。PDF 内嵌脚本不执行，外链遵守 URL 策略。
7. 当前原文件没有历史不可变快照保证，保留“当前可访问原文件”提示。未来文件替换需一起设计字节哈希/版本绑定，不能用抽取文本 checksum 证明原始文件未变更。

### 7.7 开发切片与验收

| 切片 | 交付 | 门禁 |
|---|---|---|
| P1-A1 | 纯定位、窗口构建和 schema | 精确/空白匹配、重复句、emoji、旧记录、超长块单测 |
| P1-A2 | 鉴权接口、引用关联和 checksum | 伪造引用、权限收回、版本变化、cursor 校验 |
| P1-A3 | 原文 body、窗口导航、抽屉整合 | 桌面/手机、焦点、竞态、管理页兼容 |
| P1-B1 | PDF 懒加载、翻页和降级 | 文本 PDF、扫描页、异常文件、资源回收 |
| P1-B2 | 可靠映射时的文本层定位 | 唯一命中可复核，复杂排版不误标 |

拟新增测试：`tests/test_source_preview.py`、`frontend/src/components/SourcePreviewBody.test.ts`、`frontend/src/composables/useSourcePreview.test.ts`、`tests/e2e_source_preview.py`。PDF 测试在 P1-B 创建；mocked canvas 不代替真实浏览器显示证据。

扩展现有 `tests/test_access_control.py`、`tests/test_citations.py`、`tests/test_knowledge_ingestion.py` 和 EvidencePanel/DocumentDetailDrawer/useEvidenceSelection 前端测试。使用临时知识库、SQLite 与合成文件，不修改日常上传资料。

必测：不同回答 C1 不串来源；同文档多引用；重复句、空 quote、截断、空白、emoji、跨块；版本更新、旧引用缺 checksum、旧记录缺 segments、越界页码、文件丢失；伪造 source/session/turn/citation 组合、撤权、cursor 篡改；慢请求乱序、关闭/切换期间加载、分页版本变化；320/390px、200% 缩放、焦点和 Esc；PDF 扫描/加密/损坏/超限/worker 失败及取消回收。

证据至少包含带页码 PDF、有章节的 DOCX/Markdown、重复句长文、版本更新记录。高亮必须对照后端原 text 范围校验。前端 mock 与真实后端 ACL/定位集成分别记录；只完成 P1-A 时写“抽取文本预览完成”，不得写“PDF 全面支持”。

## 8. M4：回答反馈闭环

本项不是单纯加点赞按钮，必须连通服务端持久化。第一版收集，不自动拿反馈改 prompt 或训练模型。

### 8.1 反馈接口与存储

新增 `POST /chat/feedback`；请求：`feedback_id`（客户端 UUID）、`session_id`、`turn_id`、`message_id`、`category`、`note`（最多 2,000 字符）。用户身份来自服务端 access context，不信任请求中的 user_id。

分类枚举：`incorrect_answer`、`citation_mismatch`、`outdated_policy`、`not_helpful`、`other`。响应 `{feedback_id, status: 'recorded'}`。用 Pydantic 限定字符串长度和枚举。

`backend/agent/feedback.py` 已管理业务与存储访问；在现有数据库初始化/迁移入口创建 `answer_feedback`，记录拥有者、会话、轮次、回答 ID、分类、说明、创建时间。服务端从已授权回答读取引用 ID 快照；不接受客户端提交的“原答案”作为事实。

唯一键 `(user_id, feedback_id)`。同 ID 同内容重试返回原记录；同 ID 不同内容返回 409。找不到当前用户的已提交回答返回 404；未认证沿用认证层响应。`message_id` 不能代替 user/session/turn 关联校验。

定义会话删除语义：随会话删除关联反馈，第一版不做独立长期审计留存。把删除接入 `delete_session_completely` 所使用的会话存储删除流程，并验证事务边界。反馈写入与删除沿用同一 session_operation 锁，事务内再次确认目标存在，防止删除后出现孤儿反馈。

### 8.2 前端与验收

当前入口、草稿与请求状态由 `ChatView.vue` 管理，`FeedbackDialog.vue` 负责表单及键盘/焦点，`services/api.ts` 提供提交方法；未单独新增 `AnswerFeedback.vue`。入口位于已完成回答的复制按钮旁，不在流式草稿或取消状态展示。

弹窗提供单选分类与补充说明；提交中禁用重复点击；失败保留草稿和 feedback_id 原样重试；修改分类或说明后生成新 ID，409 冲突明确提示而不自动重发；成功后关闭弹窗。关闭重开、切换会话后旧请求的成功或失败不能影响新反馈。2026-10-07 已补齐并验证这些请求行为。

同日新增专用键盘/焦点验收：通过 `overlayStack.ts` 注册模态弹层并 Teleport 到 body；打开聚焦当前选中分类，单选组只占一个 Tab 停靠点，方向键保持浏览器原生行为。Tab/Shift+Tab 在有效控件间循环，提交中跳过禁用按钮；Escape 只关闭最上层，输入法组合期间不处理 Escape。背景问答、侧栏和已打开的桌面引用面板不可交互。取消、遮罩和提交成功后返回原入口，使用 preventScroll 保持阅读位置。四浏览器 32/32、反馈重试 20/20 通过，详见 [M4 键盘与焦点验收](dsh_m4_accessibility_acceptance_2026-10-07.md)。

验收：权限隔离、伪造 message ID、重复提交、409 冲突、超长说明、空分类、网络失败保留草稿、会话删除清理。反馈数据可通过隔离测试库查询验证；管理员分析页是后续项，本阶段不宣称完成运营看板。

## 9. M5：长会话问题导航

已实现 `ConversationNavigator.vue`；数据来自当前会话用户消息与 `utils/attempts.ts`，不调用模型生成标题。验收结果见 [M5 验收记录](dsh_m5_acceptance_2026-10-06.md)。

1. 建立稳定导航项：message ID/turn ID、原问题文本摘要、对应渲染锚点。不要用数组下标当持久身份。
2. 重试属于同一问题组，默认只列一个导航项并跳到当前可见尝试；历史尝试仍可在原位置展开。
3. 桌面使用轻量问题目录，悬停/聚焦预览完整问题，点击跳转；移动端用明确按钮打开列表，不占正文横向空间。
4. 当前项跟随可见问题更新。目录主动跳转属于用户导航，不应马上被“跟随最新回答”拉回底部；提供返回最新入口。
5. 当前历史接口一次返回列表，第一版针对已加载历史即可。不要虚构服务端分页；若后续增加分页，需要一起设计“加载目标轮次后再跳转”。
6. 少量对话无需虚拟列表；先用 100 轮样例测量，必要时再引入虚拟化并处理动态高度、焦点与引用定位。

验收：2/20/100 轮、超长问题、重试折叠、切换会话清空目录、引用面板打开时跳转、键盘操作、减少动画偏好、生成中跳到旧问题后位置保持。

## 10. M6：SSE、滚动与集成边界

2026-10-07 已完成本节 SSE 与滚动专项，详见 [DSH M6 验收记录](dsh_m6_acceptance_2026-10-07.md)。最终四浏览器 56/56、前端 140 项与构建、M5 导航及产品体验回归通过；真机软键盘与历史业务/性能验收中的剩余任务仍单列，不因本专项完成而关闭。

### 10.1 SSE 加固清单

先扩展已有 `frontend/src/services/api.test.ts` 和 `frontend/src/utils/streamState.test.ts` 的行为覆盖，再依据失败修正代码：

- UTF-8 中文字节跨 chunk；事件跨 chunk；多个事件同 chunk；LF/CRLF；注释心跳；空行边界。
- 无效 JSON、事件字段无效、没有 result 就结束、收到 result 后断开、重复 done、result 后迟到的 token/status。
- HTTP 非 2xx、AbortController 取消、服务端业务回退与真实传输失败的不同展示。
- 预览 generation 切换、正式答案替换、重试与同 turn 重放不能混为一谈。

当前 transport 已有多项边界测试；先核对覆盖，不重复写只镜像实现的测试。若改用 eventsource-parser 等库，先证明现有解析器的不足，并保持正式答案判定不变。

不自动重发 POST 生成请求。恢复已提交结果应沿用 turn_id 幂等语义；用户主动重新生成采用现有 retryMessage 分组规则。DSH 物理连接重连不等于我们的 SSE 可以无条件续跑。

### 10.2 滚动

在现有 `ChatView.vue` 的 nearBottom、trackScroll、scrollToLatest、会话滚动缓存上改进；复杂时拟新增 `useConversationScroll.ts`。

区分用户滚动意图与程序动画。用户向上阅读时暂停跟随；只有回到底部或明确点“回到最新回答”才恢复。代码块/表格高度变化、输入框自动增高、引用抽屉开合不得无故改变阅读目标。使用 ResizeObserver 时解除旧节点监听，避免每个 token 测量全部 DOM。

支持 prefers-reduced-motion；小屏使用现有动态视口和安全区。模拟视口通过不等于软键盘通过，最终真机验收单列。

## 11. 开发执行方式与命令

### 11.1 每个模块的固定工作流

1. 运行 `git status --short`，记录已有修改。当前仓库有其他在途修改，不重置、不覆盖、不混入本模块提交。
2. 阅读该模块现有实现、调用者、类型和测试，先列出最小文件集合。
3. 数据功能先定 schema、空值、权限、版本兼容和错误语义，再实现后端纯函数/持久化；页面纯样式不额外制造后端修改。
4. 实现前端状态与组件；网络请求留在 services/composable，不塞进展示组件。
5. 运行受影响单测；修复后做该模块确定性浏览器场景。通过后再执行阶段构建，不边开发边堆积未验证功能。
6. 保存结果到 `work/dsh-frontend/<阶段>/`，记录测试命令、时间、退出码、浏览器、模拟/真实链路、截图及限制。
7. 检查 diff，确认没有 API key、真实用户问题、数据库、上传文件或 .env 被纳入提交。保留本地被忽略的配置文件，无需删除。
8. 补阶段完成记录；只有达到验收门禁才标记完成。提交、发布和部署按实际任务授权执行，本方案不自动触发。

### 11.2 已存在的验证命令

从仓库根目录执行，使用当前项目 Python 环境和已安装依赖：

```powershell
# M1/M2 后端基础回归；后续新增测试文件也加入相应命令
python -m pytest tests/test_conversation_context.py tests/test_api_streaming.py -q

# 前端受影响测试（按模块选择，不要求每次全跑）
npm --prefix frontend run test -- src/utils/streamState.test.ts src/services/api.test.ts src/composables/useWorkspace.test.ts
npm --prefix frontend run test -- src/components/AgentProgress.test.ts src/utils/markdown.test.ts

# P1 纯定位、引用绑定、权限、预览限制与既有入库/引用回归
python -m pytest tests/test_source_preview.py tests/test_source_preview_api.py tests/test_pdf_preview.py tests/test_access_control.py tests/test_citations.py tests/test_knowledge_ingestion.py -q
npm --prefix frontend run test -- src/components/EvidencePanel.test.ts src/components/DocumentDetailDrawer.test.ts src/utils/pdfText.test.ts src/services/api.test.ts src/composables/useEvidenceSelection.test.ts

# 阶段集成门禁
npm --prefix frontend run test
npm --prefix frontend run build

git diff --check
```

浏览器测试复用 `tests/e2e_mvp_browser.py` 的 install_api_fixtures 和现有矩阵。先在单独终端运行 `npm --prefix frontend run dev -- --host 127.0.0.1`，再按测试脚本需要运行：

```powershell
python tests/e2e_mvp_browser.py
python tests/e2e_pdf_source_preview.py
python tests/e2e_pdf_source_preview.py --browsers chrome msedge firefox webkit
python tests/e2e_feedback_retry.py --browsers chrome msedge firefox webkit
python tests/e2e_conversation_navigation.py
python tests/e2e_stream_scroll.py --browsers chrome msedge firefox webkit
# 输出目录必须不存在；每轮使用新的时间后缀，脚本会自行创建目录
$matrixRunDir = "work/dsh-frontend/matrix-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
python tests/e2e_browser_matrix.py --output-dir $matrixRunDir --browsers msedge
# 四浏览器集成验收时，另建新目录并将 browsers 改成 chrome msedge firefox webkit
```

先检查脚本依赖及浏览器可执行文件；默认 Playwright Chromium 缺失时，可在专项脚本显式使用本机 `channel='msedge'`，并准确记录为 Edge 验证，不能冒称四浏览器通过。mock fixture 必须随新增接口更新；验证 M1 真实 checkpoint 与 M4 真实落库还要跑后端集成，不能只靠前端 mock。

### 11.3 综合场景矩阵

| 场景 | 必须验证 |
|---|---|
| 空会话 | 输入框、圆环 0/未知、模式切换、建议问题 |
| 正常问答 | 预览、校验、正式结果、来源、复制、过程终态 |
| 长会话 | 摘要后占用下降、历史显示不丢失、目录跳转 |
| 长回答 | 表格/代码块/引用不撑开页面，生成结束无残留草稿 |
| 停止与失败 | 本地取消、服务端可能继续收尾、断流、回退、重试分组 |
| 切换与刷新 | 草稿和滚动恢复、旧响应隔离、用量/过程数据恢复 |
| 反馈 | 关联真实回答、幂等、权限、网络失败及删除清理 |
| 来源预览 | 定位、前后文、版本差异、权限与缺失、竞态、焦点恢复 |
| PDF 原页 | 页号、缩放、扫描件及异常降级、资源回收 |
| 窄屏 | 320/390px、横屏、200% 缩放、弹层避让 |
| 键盘 | Tab、Esc、中文输入、发送组合键、焦点返回 |
| 真机 | 软键盘遮挡、中文输入法、旋转、触控滚动（需实际设备） |

## 12. 范围控制与后续扩展

- 不把 DSH 插件系统、工作区权限、终端、代码 diff、子代理控制搬入知识问答界面。
- 附件拖放可以借鉴，但会话临时附件与知识库正式入库是不同业务；先设计生命周期、访问范围和解析状态，再做 UI。
- 完整模型上下文窗口仪表盘需独立项目：明确每次模型调用的系统提示、检索证据、工具定义、输入输出预留和模型容量；多节点不同 prompt 不能合成一个伪精确数值。
- 自动增量 Markdown 和虚拟列表需性能证据支撑；反馈和导航不能以增加按钮替代后端/状态语义。
- M1–M6 与 P1 没有默认工期承诺。按门禁拆分交付，遇到跨模型或真机限制如实记录。

## 13. 文档检查与交付记录

本文件生成后执行静态检查并对照源码复核；不因方案完成而将待开发功能标记为完成。检查范围：现有路径、测试命令、数据口径、SSE 终态、鉴权和会话隔离、候选内容边界、DSH 固定引用、模块验收与迁移要求。

检查结果见文档末尾的复核记录。本次文档任务不重新运行模型、产品浏览器矩阵或全量测试；M0 的测试数字来自本会话上一轮实际执行记录。

### 13.1 2026-10-02 生成后复核

- 已对照当前源码核实预算默认值、估算器、checkpoint、result/preview 状态与历史接口。
- 已修正过程持久化设计：复用已有 metadata_json 和历史 LEFT JOIN，并扩展元数据白名单。
- 已补充锁边界：独立 GET 加锁，锁内 result 读取不再加锁；读等待有限超时，不阻塞答案交付。
- 已修正浏览器矩阵命令：必须提供不存在的 --output-dir；单 Edge 验证与四浏览器验收分别记录。
- 已检查代码路径存在性；拟新增文件明确标记，不把计划文件当作现有实现。
- 已检查 Markdown 围栏、UTF-8 文本、固定版本源码链接格式及阶段完整性。
- 首次生成仅新增开发文档，未修改业务代码、配置或数据库；没有执行后续功能开发。

### 13.2 2026-10-02 来源预览增补与全文复核

本次更新同一文件，新增 P1-A 抽取文本定位和 P1-B PDF 原页预览，同步开发顺序、现有能力表、测试命令、综合场景及范围控制。

全文复核修正：上下文读取失败不永久显示“更新中”，result 内统计也设超时；步骤状态使用受限枚举并区分服务端/客户端计时；反馈修改后换幂等 ID，写入与删除协调；来源版本一致性与定位精度分开，旧引用不假装拥有全文版本快照。

检查覆盖现有/拟新增路径、14 个固定版本 DSH 源码链接、章节编号、代码围栏、UTF-8、空白和验证命令。新增功能仍为设计状态，本轮只编辑文档，不运行真实模型或完整浏览器回归。

### 13.3 2026-10-05 P1 实现与浏览器验收

- P1-A 已实现：受 ACL 保护的来源预览接口、引用绑定校验、checksum 版本提示、精确/空白归一化/歧义/未命中定位、有界窗口和签名 cursor。
- P1-B 已实现：`pdfjs-dist` 懒加载、授权下载、页码与缩放控件、渲染任务取消和 PDF 失败降级提示；PDF 依赖拆分为独立前端 chunk。
- 真实 Vite 页面浏览器验收通过：引用打开、文档详情、原文高亮、前后窗口、PDF 原页入口、焦点与 Escape 关闭；`tests/e2e_mvp_browser.py` 与 `tests/e2e_product_experience.py` 通过。
- 最终检查：前端 92 项测试通过，P1 相关 Python 测试 26 项通过，类型检查、生产构建和 `git diff --check` 通过。构建仅保留既有第三方 PURE 注释警告；未宣称真实模型回答质量或真机 PDF 体验。

### 13.4 2026-10-05 M5 问题目录首版

- 新增 `frontend/src/components/ConversationNavigator.vue`：按稳定 message/turn 身份展示当前会话问题目录，合并重试组并提供当前项、跳转和回到最新入口。
- `ChatView.vue` 在会话切换、历史消息和滚动时维护导航状态；移动端改为顶部按钮打开列表，不改变正文宽度。
- 浏览器长会话场景已覆盖两轮、100 轮问题目录、导航跳转、草稿和滚动位置恢复，并在 `prefers-reduced-motion: reduce` 下复测；前端回归为 93 项通过，类型检查和构建通过。
- M5 尚未宣称全部完成：更复杂重试展开与生成中跳转旧问题的专项场景仍需后续切片验证。

### 13.5 2026-10-06 M5 收尾验收

- 修正导航身份：目录项绑定重试组，新增尝试不改变目录身份；跳转到最新尝试，展开旧尝试后当前项仍对应该组。重复提交相同文本但没有重试关联的问题保留独立入口。
- 修正定位和跟随：使用相对于对话滚动容器的位置；主动导航立即暂停自动跟随，分段草稿与正式回答更新均保留阅读位置，点击回到最新后恢复跟随。
- 补齐完整问题预览、方向键/Home/End、Escape 和焦点恢复、外部点击关闭、会话切换清理、44px 触控按钮与窄屏弹出目录。
- `tests/e2e_conversation_navigation.py` 的 6 个 Chrome 场景通过：1440/390/320px 各覆盖 2/20/100 轮，普通/减少动画各覆盖分段 SSE、正式回答替换及来源面板打开时导航，四次尝试覆盖展开/折叠、稳定身份、真实重试请求和会话切换。
- 前端 95 项测试、类型检查及生产构建通过，既有产品体验浏览器回归通过。详细证据见 [M5 验收记录](dsh_m5_acceptance_2026-10-06.md)。API 与 SSE 为确定性模拟，不是在线模型质量或真机证明。
- 校正 P1 状态表：此前只验收 PDF 入口及工具栏，有效 PDF canvas 渲染缺少浏览器证据，保留为待补验收；不能从入口可见推导 PDF 完整验收。

### 13.6 2026-10-06 P1 补齐与原页专项验收

- 修复首次挂载未加载、加载期间 canvas 被移除、pdf.js 默认构建的浏览器 API 兼容问题。使用懒加载的兼容构建和独立 worker，每次仅渲染当前页；异步翻页、缩放及关闭通过取消和代次校验处理。
- 已有授权下载接口新增 `?preview=true`，默认 20 MiB / 500 页，可由 `PDF_PREVIEW_MAX_BYTES` / `PDF_PREVIEW_MAX_PAGES` 配置；普通授权下载不受该预览门禁影响。前端检查响应长度和流式累计字节；页数在本地 worker 解析后检查。
- 实现引用页码校验、默认适应宽度、50%–200% 缩放、可选择文本层与当前页查找。只高亮单一文本 run 中的唯一匹配；重复、跨 run、未命中和扫描件明确提示，未引入 OCR 或推测坐标。
- 文本预览改为每个响应窗口限制 12 块 / 24,000 code points / 单块 4,000，完整长文仍可分页和定位；修复 emoji 的前端高亮偏移。增加版本变化提示、回到引用位置、请求取消与会话清理；撤权时清除已显示的详情和预览正文。
- 保留既有详情抽屉中的原文和文档信息纵向分区；未新增独立页签或全文缓存。预览状态只保留在当前打开的界面，关闭和切换会话清理；这些界面实现差异不改变引用核验的功能门禁。
- 真实 pdf.js/worker、有效合成 PDF 的浏览器专项 15/15 通过：1440/390/320px、实际色块像素、200% 放大、文字选择和高亮落在字形像素上、24 轮连续操作、扫描/重复/损坏/加密/超限/worker 失败与重试、403/404、下载中关闭、引用第 2 页及越界页码。
- 前端 108 项、P1 相关后端 67 项通过；生产构建和既有 MVP/产品体验浏览器回归通过。生产构建的 390px 渲染与 worker 失败重试另验收 2/2 通过。浏览器 API 使用 mock，不能当作真实模型或真机质量证据。
- 证据及限制见 [P1 验收记录](dsh_p1_acceptance_2026-10-06.md)。下一阶段为 DSH M6 的 SSE 与滚动专项，历史 2026-10-02 综合验收不代替该阶段的当前检查。

### 13.7 2026-10-07 M6 SSE 与滚动专项验收

- 新增运行时事件校验；补齐中文 UTF-8 字节跨 chunk、LF/CRLF、多行 data 和心跳、完整帧边界、无效 JSON/字段、reader 取消与释放。合法过程摘要中的 pending 步骤保留；枚举字段不接受数组等隐式字符串转换。
- 第一个有效 result 为正式答案；重复结果、迟到 token/status/done、旧 preview generation 及取消请求的迟到回调不能覆盖已提交状态。done 结束读取，但缺少 result 仍按未完成处理；result 后传输断开不撤销答案。不自动重发 POST。
- 修复有执行步骤时取消提示与重新生成入口被遮蔽的问题。通过 ResizeObserver 和 RAF 处理正文、输入区及引用面板引起的布局变化，用户向上阅读时保留可见段落位置，手动回底部或点击返回最新恢复跟随。
- WebKit 初次跟随和输入框增高的失败促成跟随意图修正；Edge 的 End 原生动画与流式增高冲突改为复用立即返回最新动作。保存失败批次，最终重新执行稳定源码下的完整矩阵，不拼接旧批次声称全通过。
- 最终前端 140 项、生产构建、四浏览器 56/56、M5 6/6 与产品体验回归通过；本轮相关后端回归 26 项通过。已检查桌面和窄屏截图，页面/控制台/请求错误为空。
- 证据、失败修复与验证范围见 [DSH M6 验收记录](dsh_m6_acceptance_2026-10-07.md)。本轮未进行真实模型质量、真机或跨浏览器 PDF 专项验收；这些任务保留在 [当前状态](current_status.md)。

### 13.8 2026-10-07 PDF 跨浏览器与 M4 重试补齐

- PDF 专项扩展到 Chrome/Edge/Firefox/WebKit，各执行原有 15 项及中文制度文件，共 64/64 通过。生产构建另执行四浏览器的窄屏渲染、中文、worker 失败恢复和引用跳页，共 16/16 通过。
- 中文场景首次在四浏览器均失败，确认缺少 cMapUrl 导致部分非嵌入中文字体的字形和文本层缺失。新增 `frontend/tooling/pdfAssets.ts`，在开发服务和生产构建中提供本地字符映射、标准字体、WASM 及原包许可证；PdfSourcePreview 显式设置这些资源路径，加载仍由用户打开原页触发。
- 复核 M4 发现每次提交都换 ID 的缺口；已改为同目标、同分类、同说明的失败重试复用 ID，内容变化换 ID，并隔离旧弹窗/旧会话的迟到响应。浏览器先复现相同内容两次请求使用不同 ID，修复后四浏览器 20/20 通过。
- 最终前端 140 项、生产构建、隔离反馈存储测试 1 项、MVP 和产品体验回归通过；已查看中文 PDF 和 320px 截图。保留失败批次与预期取消记录，不将主动 reader 取消算作意外网络失败。
- 新记录见 [PDF 跨浏览器及反馈补齐验收](dsh_pdf_browser_acceptance_2026-10-07.md)。历史 P1/M6 报告保留各自当时的范围；真机、独立业务质量及性能对照仍未验收。

### 13.9 2026-10-07 M4 反馈键盘与焦点补齐

- 先用浏览器复现反馈打开后焦点仍停留在背景的问题；新增 `FeedbackDialog.vue`，复用现有弹层栈、背景 inert 与模态滚动锁定。`ChatView.vue` 保留请求身份和迟到响应隔离；`EvidencePanel.vue` 在反馈打开时隔离已有引用面板。
- 补齐打开焦点、单选组方向键及单一 Tab 停靠点、正反向焦点循环、提交中禁用按钮、输入法组合事件、顶层 Escape 关闭、取消/遮罩/成功关闭后的原入口焦点恢复。关闭使用 preventScroll，旧回答的阅读位置不变。
- 新增 `tests/e2e_feedback_accessibility.py`，Chrome/Edge/Firefox/WebKit 各 8 项，最终完整批次 32/32。既有反馈重试专项 20/20、前端 140 项与生产构建、MVP/产品体验回归通过；已查看 Chrome 320px 和 WebKit 390px 截图。
- 实际缺陷复现与测试定位器修正的批次分别保留；最终报告记录页面错误、意外控制台错误及失败请求为 0，故意返回的反馈 502 单列。证据和范围见 [M4 键盘与焦点验收](dsh_m4_accessibility_acceptance_2026-10-07.md)。模拟视口与组合事件不能替代真机软键盘和输入法验收；有效剩余任务见 [当前状态](current_status.md)。

### 13.10 2026-10-08 Demo 最终验收

- new-api 交互优化 Demo 代码已完成：`Composer.vue` 输入边界拆分，session/chat/knowledge/context 状态与动作模块，聊天状态展示规则模块，会话和知识列表 Skeleton，复制/反馈/重试的局部状态反馈，以及滚动意图回归修正。
- 最终前端测试 150/150、类型检查和生产构建通过；DSH M6 SSE/滚动专项 56/56，反馈重试 20/20，反馈键盘与焦点 32/32；MVP、产品体验用户路径和 Chrome/Edge/Firefox/WebKit 核心交互矩阵通过。
- 本轮浏览器验收使用确定性 API fixture，证明当前 Demo 交互和状态路径；不替代真机触屏/软键盘、真实模型回答质量、长期稳定性、账单成本或真实授权企业制度审核。虚拟列表和请求缓存仍按性能证据条件暂缓。
