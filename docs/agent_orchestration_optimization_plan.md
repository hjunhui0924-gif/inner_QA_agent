# 企业知识助手 Agent 编排优化实施方案

> 版本：2026-09-09。本文是给实现 agent 的执行规格。当前阶段优先保证功能可用、回答准确、来源可追溯和状态不污染；认证、精细成本计量等外围能力后置。不得新增 `reset_request_state` 节点，不得破坏现有检索来源、引用、EvidencePanel、SSE 和 Trace。

## 1. 分阶段原则

### 阶段一：当前优先实现

- 扩充知识文档和评测题。
- 验证并补齐 `create_turn_state()` 的请求级默认值。
- 保留文件来源、标题、`source_id`、`chunk_id` 和引用展示。
- 让生成候选与正式会话消息隔离。
- 幻觉重试携带失败原因，并严格限制次数。
- 记录统一的基础调用次数和延迟。
- 增加离线回归和真实 RAG 验证。

### 阶段二：可用版本稳定后实现

- 独立 `RequestBudget` 模块。
- Token、模型价格和费用统计。
- deadline、总调用数和总成本硬限制。
- 工具结构化返回和工具结果校验。
- 更完整的 SSE、Trace 和报告字段。
- 答案完整性检查和灰区相关性 Judge。

### 阶段三：多用户企业上线前必须实现

- 服务端认证和身份解析。
- 角色、部门、scope 权限。
- 检索层权限过滤。
- 敏感文档审计和数据保留策略。

阶段三可以暂缓于本地单用户开发和内部效果验证，但未完成前不得宣称系统满足多用户企业生产安全要求。

## 2. 目标流程和最小调整

```text
create_turn_state → manage_conversation_context → route_query
  ├─ rag：retrieve → grade_documents → generate（不相关时改写后重检索）
  ├─ tool_call：tool_executor → generate
  └─ direct：generate
所有 generate → check_hallucination
  ├─ 接受：commit_answer → END
  ├─ 可重试：generate（读取 generation_instruction）→ 再次校验
  └─ 终止失败：fallback_answer → commit_answer → END
检索重试耗尽 → fallback_answer → commit_answer → END
```

这是目标流程，不是现状描述。三个路由都必须到达正式提交路径。第一阶段保留 direct/tool 的现有语义校验跳过行为，但仍检查生成错误和空答案；跳过 grounding 不表示已验证外部事实。不得要求 direct/tool 必须带知识库引用。第二阶段再增加工具结果校验。

只新增一个 `commit_answer` 节点，复用 `generate` 完成重生成，不新增 `regenerate_with_constraints`。最终 fallback 也统一经 commit，避免重复写消息。

## 3. 状态字段规范

`backend/agent/sessions.py:create_turn_state()` 是请求初始化入口，不新增 `reset_request_state` 节点。会话级字段必须保留；请求级字段每轮显式初始化。

| 字段 | 类型 | 初始值 | 生命周期 | 主要写入者 |
|---|---|---|---|---|
| `messages` | list | 本轮 HumanMessage | 会话级 | `commit_answer` |
| `conversation_summary` | str | 保留旧值 | 会话级 | 上下文管理 |
| `query` | str | 本轮问题 | 请求级 | API |
| `rewritten_query` | str | `""` | 请求级 | rewrite |
| `retrieved_docs` | list | `[]` | 请求级 | retrieve |
| `retrieval_metadata` | dict | `{}` | 请求级 | retrieve |
| `is_relevant` | `bool \| None` | `None` | 请求级 | grade |
| `answer` | str | `""` | 请求级 | commit |
| `candidate_answer` | str | `""` | 请求级 | generate/fallback |
| `candidate_citations` | list | `[]` | 请求级 | citation |
| `answer_disposition` | pending/accepted/fallback | pending | 请求级 | 校验/fallback |
| `turn_id` | str | 新 UUID | 请求级 | API |
| `generation_instruction` | str | `""` | 请求级 | retry |
| `citations` | list | `[]` | 请求级 | commit |
| `tool_result` | dict \| None | `None` | 请求级 | tool |
| `tool_output` | str | `""` | 请求级 | tool/兼容渲染 |
| `hallucination_pass` | `bool \| None` | `None` | 请求级 | grounding |
| `hallucination_reason` | str | `""` | 请求级 | grounding |
| `retrieval_retry_count` | int | `0` | 请求级 | rewrite |
| `hallucination_retry_count` | int | `0` | 请求级 | grounding |
| `failure_stage` | str \| None | `None` | 请求级 | 失败节点 |
| `failure_reason` | str \| None | `None` | 请求级 | 失败节点 |
| `request_call_count` | int | `0` | 请求级 | 所有调用 |
| `model_call_count` | int | `0` | 请求级 | 模型调用 |
| `tool_call_count` | int | `0` | 请求级 | 工具调用 |
| `total_latency_ms` | float | `0.0` | 请求级 | 调用记录 |
| `budget_snapshot` | dict \| None | `None` | 请求级摘要 | 预算模块 |

同步更新 `backend/agent/state.py` 的 `AgentState`：`is_relevant` 和 `hallucination_pass` 使用 `bool | None`，新增字段必须显式标注。`tool_result` 必须是 JSON 可序列化的普通 dict。

`budget_snapshot` 只保存可序列化摘要，例如调用数、Token、耗时、估算费用和是否耗尽。真实的 `RequestBudget` 控制对象不放进 checkpoint；它属于本轮运行上下文，由 API/graph invocation 创建并在本轮结束后释放。传递方式在第 5.1 节固定说明；不能用 snapshot 代替真实预算控制。请求级字段可能被 checkpoint 保存，但下一轮必须重置，不代表跨轮复用。`tool_result`、`budget_snapshot` 属于第二阶段，第一阶段无需为它们实现外围功能。

## 4. 第一阶段：准确性和可用性

### 4.1 来源保护

保持 `Document.metadata` 中的 `source_id`、`chunk_id`、标题和文件路径。任何检索、重排、引用或重试改动都必须保留这些字段。`EvidencePanel.vue` 继续使用现有 citation 结构；新增字段只能可选追加。

验收：运行 citation 测试、API SSE 测试，并完成至少一条真实 RAG 请求，确认回答中的引用可以定位到文件和 chunk。

### 4.2 失败草稿隔离和正式消息提交

当前 `generate()` 不得返回 `messages: [AIMessage(...)]`。它只写 `candidate_answer`、候选 citations 和必要的错误信息。

新增 `commit_answer` 节点作为唯一的正式 AIMessage 写入者：

- `generate` 写 `candidate_answer`、候选引用，不写 `answer` 或 `messages`；校验读取候选而不是空的 `answer`。
- 接受路径只允许非空、无生成错误的候选；RAG 还必须通过现有引用和 grounding 校验。
- `fallback_answer` 构造最终拒答候选、清空候选引用，标记 `answer_disposition="fallback"`，不再直接写消息。通过校验时标记为 `accepted`。
- commit 仅接受 accepted/fallback，复制候选到 `answer`，写正式 `citations` 和一条 AIMessage，然后清空候选及重试指令。pending 不允许提交。
- API 从最终 graph state 读取正式答案并写业务 SQLite，不再从 generate 节点结束事件提取可交付答案。保留现有 SSE 事件及已校验答案分块行为，不流出失败候选。
- 为每轮分配稳定 turn ID，提交消息使用稳定消息 ID；重放或恢复时避免重复追加。不要把 session ID 当 turn ID。

不能只在 API 层写业务 SQLite：正式答案必须进入 LangGraph checkpoint，才能参与下一轮上下文。测试应同时检查 checkpoint messages、业务 SQLite 和 SSE 的最终答案一致且只有一份，失败候选均不出现。

### 4.3 幻觉重试

校验失败时保存 `hallucination_reason`，并据此构造 `generation_instruction`。条件边返回现有 `generate`；generate 把该指令加入 Prompt，要求删除无证据内容或安全拒答。失败原因只作为修正反馈，不作为新证据。每次生成覆盖候选答案和候选引用，下一轮初始化清空指令。默认最多一次受限重生成。

当前 `hallucination_retry_count` 是失败次数；若保留该语义，比较符和递增位置必须整体测试，不能单独改 `>` 或 `>=`。每个 RAG 候选答案都必须重新经过 citation 和 grounding 校验；预算或超时耗尽时不得交付未经校验的答案。

### 4.4 失败分类

内部主字段只使用 `failure_stage` 和 `failure_reason`。阶段值：`retrieval`、`relevance`、`evidence`、`citation`、`generation`、`hallucination`、`tool`、`runtime`。

外部兼容字段必须保留 `failure_type`、`fallback_reason`、`generation_error`。现有 SSE 和 Trace 已输出 failure_type，评测也有自己的 FailureType 枚举。不得删除或改名，不把这些字段变成独立手工维护的新真相源。

运行时映射复用并扩展 `classify_runtime_failure`，新增内部字段缺失时继续执行原分类逻辑：

| 内部终止结果 | 外部 failure_type | fallback_reason | generation_error |
|---|---|---|---|
| retrieval/relevance 重试耗尽 | retrieval_miss | retrieval_exhausted | 空 |
| generation 模型异常 | generation_error | generation_error | 保留原异常摘要 |
| hallucination 重试耗尽 | generation_error | hallucination_exhausted | 空 |
| citation 校验终止失败 | citation_error | hallucination_exhausted | 空 |
| 最终成功 | none | 空 | 空 |

证据不足、工具异常等未覆盖项沿用现有兼容分类，同时追加内部 stage/reason，实施时用契约测试锁定行为。评测依赖 gold labels 的 ranking_error、abstention_error 等不能机械映射为运行时分类，保留已有评测枚举和算法。重试成功后清空当前失败状态；先前失败保留在 trace 的尝试记录中。

## 5. 第二阶段：成本、工具和外围工程

### 5.1 RequestBudget

新增 `backend/observability/budget.py`，使用 dataclass。预算对象只存在于本轮运行上下文，不写入 checkpoint；AgentState 只保存 `budget_snapshot`。

```python
@dataclass
class RequestBudget:
    max_model_calls: int
    max_tool_calls: int
    max_total_seconds: float
    model_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: float = 0.0

    def ensure_available(self) -> None: ...
    def consume_model_call(self, node: str) -> None: ...
    def record_usage(self, *, input_tokens: int, output_tokens: int, cost: float) -> None: ...
    def snapshot(self) -> dict[str, object]: ...
```

价格来源、response metadata Token 字段和预算耗尽后的统一 fallback 在该模块集中定义。路由、摘要、改写、生成、grounding 和标题生成都必须计入模型调用统计。

统一统计字段为 `request_call_count`、`model_call_count`、`tool_call_count`、`total_latency_ms`。预算对象内部可以保留更细字段，但不得再引入另一套 AgentState 统计名称。

#### 预算对象传递（第二阶段）

采用 LangGraph Runtime context：实现前核实依赖版本，当前 requirements 的 `langgraph>=0.2.0` 不保证支持此接口。第二阶段选择并锁定经过验证的 Runtime/context_schema 版本，同步 checkpoint 依赖并跑持久化兼容测试。

```python
@dataclass
class RunContext:
    budget: RequestBudget

# build_graph 中：StateGraph(AgentState, context_schema=RunContext)
# 节点签名：async def generate(state: AgentState, runtime: Runtime[RunContext])
# 节点共享：runtime.context.budget
# API 创建 context 一次，并传给 graph.astream_events(..., context=context)
```

所有需要调用计量的节点接收 runtime。API 内的标题生成也复用同一个 context.budget，不创建第二份预算。graph 返回后再发生标题调用时，以 API 完成后的 snapshot 作为请求最终 Trace 统计，checkpoint 快照仅是 graph 完成时的状态。不得使用进程全局预算对象或把对象放入可被持久化的 configurable。

预算检查和额度预留在调用前完成，失败尝试也计数；模型 SDK 内部重试需关闭或显式纳入计量。定义 request_call_count=model_call_count+tool_call_count；工具内部的模型调用仍计入 model_call_count。total_latency_ms 表示本轮入口到结束的墙钟耗时，不是各节点耗时之和。单节点耗时另存 Trace。第一阶段仅做基础统计，不宣称已具备硬成本预算。

### 5.2 工具兼容

内部增加：

```python
tool_result = {"ok": True, "text": "...", "sources": [], "error": None}
```

保留 `tool_output: str`，由结构化结果渲染得到，保证现有 `generate()`、SSE 和前端兼容。网页搜索在拥有结构化来源前不强制增加 grounding Judge。

## 6. 第三阶段：权限 seam

多用户企业上线前实现：

```text
认证中间件 → ServerAccessContext → KnowledgeAccessPolicy
  → RetrievalFilter → 按权限约束向量/BM25召回 → 防御性权限复核 → RRF/Rerank → 引用
```

优先把权限过滤下推到向量和 BM25 的召回范围，避免未授权候选占用 top-k；召回后再复核，权限过滤必须在 RRF 和 Rerank 之前完成。仅在召回后过滤不能保证召回数量充足或消除全部存在性侧信道。不能让未授权文档参与排序后再隐藏，否则会产生排名偏差、文档存在性侧信道、召回数量不足和引用状态不一致。

建议接口：

```python
@dataclass(frozen=True)
class ServerAccessContext:
    user_id: str
    roles: frozenset[str]
    departments: frozenset[str]
    scopes: frozenset[str]
```

认证中间件从可信身份提供方解析该对象；不能从请求体的 `user_id` 或 `access_scope` 直接创建。`KnowledgeAccessPolicy` 根据 context 对文档 metadata 执行过滤，重排和引用只能处理已授权文档。前端隐藏文件不构成权限控制。

第三阶段 ACL 初版规则：默认拒绝；缺少或非法权限元数据不视为公开。显式 deny 优先于 allow；文档 scopes 采用 all-of（用户 scopes 必须覆盖 required scopes），不支持隐式通配符；部门授权精确匹配用户明确部门，不支持向上或向下继承。显式 public-internal 标记仅向已认证内部用户开放。历史文档需人工确认并迁移 ACL，不能为兼容而默认公开。会话、来源查看/下载、文档管理入口同样需要服务端授权。

## 7. 测试和验收

第一阶段拆成三个可分别验收的小任务，认证和精细预算不得混入：

1. 状态隔离：补默认值、候选/正式答案分离、所有路由 commit、checkpoint/SQLite/SSE 一致性及来源回归。
2. 失败重试：generate 读取修正指令、失败字段兼容映射、0/1/2 次重试边界和失败恢复测试。
3. 基础调用统计：统一调用口径和墙钟耗时，覆盖失败调用与 API 标题调用。知识文档和评测数据扩充可并行，但不能代替这三项行为测试。

付费在线评测先运行固定 regression 子集；结果无明显回退后，按阶段里程碑运行更大的 development 集。held_out 只用于阶段验收，不能反复查看并调参。不要把题数写成固定全量要求：实际题数从数据集读取（当前企业制度集为 414 题）。记录实际运行样本数、模型与调用量，不将离线结果冒充在线结果。

第一阶段必须有：

- 现有测试全部通过。
- `AgentState` 新字段类型和初始化测试。
- 同 session 两轮状态残留测试。
- 失败草稿不进入 history、最终答案进入 checkpoint 测试。
- retry 计数边界测试。
- 引用、source_id、chunk_id 和 EvidencePanel 回归测试。
- no-answer、无召回、引用错误和生成错误测试。
- 真实 RAG 请求和离线评测结果。
- 统一调用次数和延迟记录。

第二阶段增加预算对象、snapshot、Token、费用和工具 schema 测试。

第三阶段增加越权检索、跨部门文档、角色变化和审计日志测试。

## 8. 实现 agent 的交付内容

必须报告修改文件、阶段范围、测试命令和结果、来源展示验证、准确性指标、调用次数、延迟、暂缓功能及其上线限制。不得只报告“测试通过”。

## 12. 模块化实施与测试闸门

开发必须采用“一个模块完成并验证后，再进入下一个模块”的顺序，避免多个改动同时发生而无法定位回归原因。每个模块都要先读取当前实现，明确修改范围，完成代码后运行针对性测试，再运行全量测试；测试未通过时停止进入下一模块。

统一循环：

```text
读取现状 → 明确改动 → 实现一个模块 → 针对性测试
→ 全量测试 → 来源/SSE 回归 → 记录结果 → 进入下一模块
```

### 模块 1：状态初始化与隔离

修改 `create_turn_state()` 和 `AgentState`，补齐请求级字段，明确会话级字段保留规则。不得新增 `reset_request_state` 节点。

测试：新字段类型、默认值、同 session 两轮请求、旧检索文档/引用/答案/计数不残留，摘要和历史保留。

通过条件：状态隔离测试和现有全量测试通过。

### 模块 2：候选答案与正式消息提交

让 `generate()` 只写 `candidate_answer` 和候选引用；增加 `commit_answer`，统一处理 RAG、direct、tool_call 和 fallback。只有通过校验的候选或最终 fallback 才写入 LangGraph `messages`。

测试：checkpoint、业务 SQLite、SSE 最终答案一致；失败候选不出现在三者中；文件来源和 EvidencePanel 数据不变。

通过条件：三条回答路径测试、持久化测试、SSE 和 citation 回归通过。

### 模块 3：幻觉失败重试

复用 `generate()`，让它读取 `generation_instruction` 和 `hallucination_reason`。重试必须携带失败原因并改变约束。保持 retry 计数语义一致，覆盖配置为 0、1、2 的情况。

测试：失败原因进入下一次 Prompt；每个候选都重新校验；失败草稿不进入历史；达到上限后安全 fallback。

通过条件：重试边界测试和全量测试通过，且没有未经校验的答案被提交。

### 模块 4：失败分类与兼容输出

内部使用 `failure_stage`、`failure_reason`；保留现有外部 `failure_type`、`fallback_reason`、`generation_error`。通过统一映射扩展信息，不能删除或改名已有字段。

测试：检索、相关性、证据、引用、生成、幻觉和运行时失败均能定位；旧 SSE、Trace 和评测报告字段保持兼容。

通过条件：失败分类测试和 API/Trace 回归通过。

### 模块 5：基础调用统计

统一记录 `request_call_count`、`model_call_count`、`tool_call_count`、`total_latency_ms`。第一阶段只做统计，不实现完整费用预算。

测试：路由、摘要、改写、生成、grounding、工具和标题调用均按实际发生记录；失败调用也计入；总数满足 `request_call_count = model_call_count + tool_call_count`。

通过条件：统计测试、SSE/Trace 回归和全量测试通过。

### 模块 6：知识库与评测集扩充

增加多主题文档、版本信息、来源指纹和分层评测题。先校验数据格式和 provenance，再运行检索 benchmark。在线评测先跑 regression 集，阶段性再运行 development 和 held_out。

当前已落地 50 条知识记录、8 个业务域和 414 道企业制度评测题；新增制度为带公开参考来源的合成测试材料，详情见 `docs/knowledge_base_expansion.md`。完整统一在线评测仍需单独重新运行，不能用离线结果替代。

通过条件：数据校验通过，来源可追溯，检索指标不低于基线，且不可回答题没有明显误答回退。

### 模块 7：工具结构化返回

增加内部 `tool_result`：`ok`、`text`、`sources`、`error`；保留字符串 `tool_output` 供现有生成、SSE 和前端使用。暂不强制网页搜索 grounding Judge。

测试：时间工具、搜索成功、搜索失败、空结果和旧字符串兼容行为。

通过条件：工具测试和现有 API/SSE 测试通过。

### 模块 8：RequestBudget

新增 `RequestBudget` dataclass。真实对象只存在于本轮 Runtime context，不进入 checkpoint；`AgentState` 只保存可序列化的 `budget_snapshot`。统一检查模型调用、工具调用、deadline、Token 和估算费用。

测试：预算耗尽、超时、Token 统计、费用计算、快照序列化、失败调用计数和标题调用计数。

通过条件：预算测试、持久化兼容测试和全量测试通过。

### 模块 9：服务端认证与权限过滤

多用户企业上线前实现：

```text
认证中间件 → ServerAccessContext → KnowledgeAccessPolicy
→ RetrievalFilter → 按权限约束向量/BM25召回 → 权限复核 → RRF/Rerank → 引用
```

默认拒绝；缺少权限元数据不视为公开；显式 deny 优先；部门精确匹配，暂不支持部门继承。客户端 `user_id` 和 `access_scope` 不得作为授权依据。

测试：未认证、越权、跨部门、角色变化、缺少 ACL、来源查看和下载权限。

通过条件：权限测试全部通过后，才允许多用户企业部署。

### 模块间依赖

必须按以下顺序推进：

```text
状态隔离
→ 正式消息提交
→ 幻觉重试
→ 失败分类
→ 基础调用统计
→ 知识库/评测扩充
→ 工具结构化
→ RequestBudget
→ 认证与权限
```

知识库数据整理可以提前准备，但不要用新数据掩盖检索逻辑或状态逻辑的回归。权限设计可以提前记录，但实现和上线门槛放在最后一个模块。

### 停止条件

出现以下任一情况时，停止当前模块并修复后再继续：

- 现有测试失败。
- 文件来源、chunk 或引用展示回归。
- SSE 事件无法被现有前端解析。
- checkpoint 与业务 SQLite 的正式答案不一致。
- 失败候选进入正式历史。
- 评测指标低于基线且没有解释。
- 新增模型调用没有统计或超出当前阶段范围。
