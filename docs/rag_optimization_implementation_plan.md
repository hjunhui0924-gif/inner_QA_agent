# RAG 知识库与评测体系优化实施计划

## 1. 目标

将当前客服问答 Agent 从“具备 RAG 功能和少量验证样例”推进到“有足够知识覆盖、可重复评测、能定位失败原因、支持持续迭代”的状态。

本计划优先解决两个问题：

1. 知识库文档数量少，无法代表真实企业客服场景。
2. RAG 评测题数量和类型不足，容易出现局部优化或指标失真。

## 2. 当前基线

以当前仓库为准：

- `data/knowledge_base.json`：7 条种子知识。
- `data/eval_corpus/documents/`：4 份法规文档。
- `data/evals/official_policy_retrieval_eval.json`：34 道题，30 道可回答，4 道无答案。
- `data/evals/dongshan_legal_opinion_eval.json`：8 道题。
- 官方政策语料切分结果：4 份文档、46 个 chunk。
- 已有能力：混合检索、RRF、Rerank、引用、安全拒答、自动裁判、追踪和 benchmark 脚本。

已有测试覆盖了很多模块行为，但不能代替真实问题集。后续 agent 开始实现前，应先运行现有测试并保存基线结果。

## 3. 目标结果

第一阶段完成后应达到：

- 至少 15–20 份主题文档。
- 至少 150 道 RAG 评测题，建议最终达到 200–300 道。
- 评测集分为 `development`、`regression`、`held_out` 三类。
- 可回答题和不可回答题比例约为 75:25。
- 每道题具有来源、证据短语、问题类型、难度和拒答要求等标注。
- 每次 benchmark 都记录语料版本、代码 commit、模型、参数和完整失败明细。
- 能区分检索失败、证据不足、引用失败、生成错误和拒答错误。
- 建立 CI 可运行的轻量回归评测，不依赖线上大模型调用。

## 4. 实施阶段

### 阶段 0：建立基线

执行：

```powershell
python -m pytest -q
python scripts/run_retrieval_benchmark.py
python scripts/run_answer_benchmark.py
python scripts/run_rag_eval.py
```

记录：

- 测试通过数和失败数。
- Recall@1、Recall@3、Recall@5。
- MRR 或其他已有排序指标。
- no-answer accuracy。
- 引用准确率、答案通过率和平均延迟。
- 当前 git commit、Python 版本、依赖版本。

如果某个脚本需要 API key 或外部服务，应提供离线模式或明确跳过原因，不要伪造结果。

### 阶段 1：扩充知识库文档

在 `data/knowledge_base.json` 或新的结构化数据目录中补充以下主题：

| 主题 | 建议文档 | 重点问题 |
|---|---|---|
| HR | 入职、离职、请假、年假、加班、调休、考勤 | 条件、审批人、时限 |
| 财务 | 报销、差旅、付款、发票、借款 | 金额阈值、材料、时限 |
| 采购 | 采购申请、供应商、比价、合同 | 金额和审批链 |
| IT | 账号、权限、VPN、设备、生产环境 | 权限等级和审批 |
| 法务 | 合同、印章、保密、知识产权 | 标准模板和升级条件 |
| 行政 | 会议室、办公用品、访客、资产 | 预约和责任人 |

最低要求：每个主题 2–4 份文档，总数达到 15–20 份。文档应包含：

- 一份当前有效版本。
- 一份包含相似关键词的干扰文档。
- 对关键主题增加一份旧版本或废止版本，用于测试版本过滤。

建议把知识项统一为如下格式：

```json
{
  "id": "hr-leave-v2",
  "title": "请假与调休流程",
  "content": "...",
  "source": "internal_policy",
  "department": "HR",
  "version": "v2",
  "status": "active",
  "effective_from": "2026-01-01",
  "effective_to": null,
  "owner": "人力资源部",
  "access_scope": "internal",
  "original_filename": "请假与调休流程-v2.md"
}
```

实现要求：

1. 修改入库流程，使元数据进入每个 chunk。
2. 检索结果保留 `source_id`、标题、版本和生效时间。
3. 对 `status`、`effective_from`、`effective_to` 预留过滤能力。
4. 旧版本不能在问题明确询问现行规则时排在当前版本之前。
5. 保留来源指纹或 checksum，避免文档被无意修改后仍被当作同一版本。

不要直接大量复制未经核验的互联网政策作为“企业内部制度”。内部制度可以使用清晰的合成文档，但必须在 metadata 中标记来源类型，例如 `synthetic_seed`。

### 阶段 2：重构评测数据格式

新增或扩展评测 schema。建议每道题至少包含：

```json
{
  "id": "hr-leave-001",
  "split": "development",
  "category": "conditional_policy",
  "domain": "HR",
  "difficulty": "medium",
  "question": "连续请假四天需要通知哪些人？",
  "answerability": "answerable",
  "source_ids": ["hr-leave-v2"],
  "evidence_phrases": ["连续请假超过三天", "部门负责人", "人力资源"],
  "expected_facts": ["需要同步抄送部门负责人和人力资源"],
  "must_cite": true,
  "expected_refusal_reason": null,
  "tags": ["threshold", "approval_chain"]
}
```

不可回答题示例：

```json
{
  "id": "hr-leave-una-001",
  "split": "regression",
  "category": "no_answer",
  "domain": "HR",
  "question": "公司是否提供海外长期派驻补贴？",
  "answerability": "unanswerable",
  "source_ids": [],
  "evidence_phrases": [],
  "expected_facts": [],
  "must_cite": false,
  "expected_refusal_reason": "knowledge_missing",
  "tags": ["out_of_corpus"]
}
```

数据规模建议：

- `development`：100–150 题，用于调参。
- `regression`：40–60 题，用于每次提交前检查。
- `held_out`：50–100 题，仅在阶段性评估时运行。
- 每个主题至少 20 题。
- 每个主题至少包含直接事实、条件规则、阈值、同义改写、多文档、冲突/旧版本和不可回答题。

### 阶段 3：构造高价值难例

不要只增加同类事实题，应覆盖：

1. 同义表达：报销、费用报销、差旅费用申请。
2. 条件组合：金额阈值 + 审批人 + 所需材料。
3. 否定问题：哪些情况不需要法务复核？
4. 多跳问题：先申请什么，再由谁审批，最后补什么材料？
5. 多文档问题：规则分散在两份制度中。
6. 时间版本：现行制度与旧制度同时存在。
7. 相似干扰：关键词相同但适用部门不同。
8. 不可回答：知识库没有依据的问题。
9. 语言噪声：口语、省略主语、错别字和较长上下文。
10. 引用要求：答案中的每个关键结论都必须能回到证据 chunk。

每类至少准备 10 道题，并在 `tags` 中标记，方便按类型统计。

### 阶段 4：完善评测指标和失败分类

检查 `backend/evaluation/` 的现有接口，在不破坏现有报告格式的前提下增加以下字段：

- `retrieval_failure`：没有召回正确来源。
- `evidence_failure`：召回来源正确，但没有包含关键证据。
- `ranking_failure`：正确证据被召回，但排名过低。
- `citation_failure`：答案引用缺失、引用不存在或引用与结论不对应。
- `generation_failure`：答案超出证据、事实错误或遗漏关键条件。
- `refusal_failure`：应拒答却回答，或可以回答却错误拒答。
- `tool_or_runtime_failure`：超时、模型错误、索引缺失等。

评测报告至少输出：

- 总体指标。
- 按 split、domain、category、difficulty、tag 的指标。
- 每种失败类型的数量和比例。
- 失败题的 query、召回 chunk、gold source、答案和判定原因。
- 不同 retrieval strategy 的对比。
- 延迟、调用次数和估算成本。

### 阶段 5：加入轻量回归门禁

在 CI 或本地检查中运行 `regression` 集。建议分两层：

1. 离线确定性层：测试 schema、来源校验、检索指标、引用格式、无答案路由和报告结构。
2. 可选模型层：需要 API key 时运行答案评测，记录模型和时间，不作为每次 PR 的唯一硬门禁。

初始门槛建议：

- 数据校验：100% 通过。
- gold source 校验：100% 通过。
- no-answer accuracy：不低于 0.90。
- source hit@5：不低于当前基线。
- evidence recall@5：不低于当前基线。
- regression 集不能出现新增的高优先级引用错误。

阈值应在获得新基线后写入配置，不要凭空设定过高目标。

### 阶段 6：加入线上样本回流

在现有 tracing 能力上补充匿名化评测样本记录：

- 用户问题。
- 使用的模式和知识库版本。
- 召回策略、top-k、耗时和降级原因。
- 返回的 source/chunk 标识。
- 是否拒答、是否有引用。
- 用户反馈或人工标注结果。

不要默认记录敏感原文；至少提供脱敏、关闭记录和保留期限配置。人工每周从失败样本中挑选题目，加入 development 或 regression 集，并记录加入原因。

## 5. 建议改动文件

优先检查和修改：

- `data/knowledge_base.json`
- `data/eval_corpus/manifest.json`
- `data/eval_corpus/provenance.json`
- `data/evals/official_policy_retrieval_eval.json`
- `data/evals/dongshan_legal_opinion_eval.json`
- `backend/knowledge/chunking.py`
- `backend/knowledge/deduplication.py`
- `backend/retrieval/engine.py`
- `backend/evaluation/retrieval.py`
- `backend/evaluation/answers.py`
- `backend/evaluation/judge.py`
- `backend/observability/tracing.py`
- `scripts/run_retrieval_benchmark.py`
- `scripts/run_answer_benchmark.py`
- `scripts/run_rag_eval.py`
- `tests/test_knowledge_ingestion.py`
- `tests/test_retrieval_evaluation.py`
- `tests/test_citations.py`
- `tests/test_generation_safety.py`

先阅读现有 schema、报告字段和测试，再决定是否新增文件。尽量复用现有 benchmark 和 provenance 机制。

## 6. 验收清单

- [ ] 现有测试全部通过。
- [ ] 新增文档均有稳定 ID、来源和 checksum。
- [ ] 每个 chunk 都带有可追溯 metadata。
- [ ] 评测题通过 schema 和 gold evidence 校验。
- [ ] 三个 split 能独立运行。
- [ ] 至少 150 道题，至少 6 个业务主题。
- [ ] 可回答/不可回答题比例符合设计。
- [ ] 报告可以按领域和失败类型聚合。
- [ ] 能复现一次完整 benchmark。
- [ ] regression 集有明确门槛。
- [ ] 评测结果记录代码、语料、模型和参数版本。
- [ ] 失败案例能定位到检索、证据、引用、生成或运行时阶段。
- [ ] 文档中没有把合成制度误标为真实公司制度。

## 7. 执行顺序建议

另一个 agent 应按以下顺序实现：

1. 运行测试和 benchmark，保存基线。
2. 阅读现有评测 schema、入库流程和报告生成逻辑。
3. 先扩展 schema 与校验，不急于批量添加题目。
4. 添加 15–20 份结构化知识文档及 provenance。
5. 添加 150 道以上分层评测题。
6. 修改评测报告，加入分组指标和失败分类。
7. 增加离线 regression 测试与门槛。
8. 运行完整验证，修复数据和实现问题。
9. 更新 README，说明数据生成、评测和复现命令。
10. 最后再考虑切块和检索参数实验。

## 8. 交付要求

交付时必须提供：

- 修改文件列表。
- 新增文档和评测题数量。
- 基线与优化后的指标对比。
- 执行过的命令及结果。
- 未能运行的命令及原因。
- 仍存在的风险和下一步建议。

不要只报告“测试通过”。必须说明新增数据是否真的扩大了主题、问题类型和不可回答场景覆盖。
