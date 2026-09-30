# 统一 RAG 评测

项目使用一个评测目录管理三套来源数据，共 456 道题：

题集和企业语料已更新到 456 题。2026-09-30 已完成修复后的完整在线检索评测，报告 `data/eval_reports/unified_rag_benchmark.json` 标记 `offline=false`，覆盖 456 题；整体 Source Hit@4 和 Evidence Recall@4 均为 100%，两个阻断门禁通过，检索失败和证据失败均为 0。报告未包含生成/Judge 结果。

| 数据集 | 题数 | 语料 | 角色 | 门禁 |
| --- | ---: | --- | --- | --- |
| `official_policy` | 34 | `official_policy_corpus` | 高风险法规安全集 | 阻断 |
| `enterprise_rag` | 414 | `enterprise_knowledge_base` | 主回归集 | 阻断 |
| `dongshan_legacy` | 8 | `enterprise_knowledge_base` 的旧单文档子集 | 兼容性参考 | 不阻断 |

三套数据被统一成相同的评测字段，并通过 `dataset_id` 和 `corpus_id` 保留来源信息。运行时仍为每套数据构建独立检索引擎，不把法规、企业制度和旧单文档混进同一个检索池。

## 运行命令

默认执行 456 道题的在线检索评测，使用当前配置的 `qwen3.7-text-embedding` 和 `gte-rerank-v2`：

```powershell
python scripts/run_unified_rag_benchmark.py --mode retrieval --top-k 4
```

离线模式只用于快速验证入口、数据结构和报告格式，结果不会标记为正式质量门禁：

```powershell
python scripts/run_unified_rag_benchmark.py --offline --mode retrieval
```

运行单个数据集或少量题目进行冒烟测试：

```powershell
python scripts/run_unified_rag_benchmark.py --dataset official_policy --limit 1
```

需要执行生成评测时使用 `answer`；需要同时调用在线 Judge 时再加 `--enable-judge`。答案模式沿用阶段一的 `generate()` 候选输出接口，并记录生成重试、调用次数和耗时；它用于统一答案层评测，不等同于包含路由、查询改写和正式提交的完整生产 Agent 端到端验收。`all` 与 `answer` 的区别是 `all` 保留同一份报告中的检索和回答结果：

```powershell
python scripts/run_unified_rag_benchmark.py --mode all --enable-judge
```

报告默认写入：

```text
data/eval_reports/unified_rag_benchmark.json
```

报告同时提供两种总体视图：`case_weighted` 按题目数量加权，适合观察整体样本表现；`dataset_macro_average` 对法规、企业和旧文档三个数据集等权，适合避免 414 道企业题掩盖 34 道法规题或 8 道兼容题的局部问题。总体视图不替代每个数据集自己的门禁。

## 指标解释

检索指标包含 Source Hit、Evidence Recall、MRR、NDCG、失败分类和延迟。不可回答题不会参与 Source Hit 或 Evidence Recall；报告额外记录是否返回了候选文档，并明确标记 `gold_evidence_support_proxy` 不是生成层拒答评测。

回答模式还会记录 Gold Phrase、数字匹配、引用出处、引用完整性、拒答和可选 LLM Judge 指标。阶段一的 `generate()` 同时存在候选字段和正式提交字段，评测读取正式字段，只有在直接生成结果没有正式答案时才回退到候选字段。

## 检索策略消融

使用 `scripts/run_retrieval_ablation.py` 在同一语料和题集上比较策略；默认是企业主回归集，也可用 `--split development`、`--split regression` 或 `--split held_out` 单独运行：

```powershell
python scripts/run_retrieval_ablation.py --dataset enterprise_rag --top-k 5
python scripts/run_retrieval_ablation.py --dataset enterprise_rag --split regression --top-k 5
```

2026-09-30 的企业 414 题在线结果见 `data/eval_reports/enterprise_retrieval_ablation_20260930.json`：

| 策略 | Source Hit@5 | Evidence Recall@5 | MRR@5 | P50 延迟 |
| --- | ---: | ---: | ---: | ---: |
| 向量 | 99.70% | 99.70% | 87.99% | 201 ms |
| 词法（BM25 式） | 99.70% | 99.70% | 95.09% | 0.4 ms |
| RRF 融合 | 99.70% | 99.70% | 90.91% | 204 ms |
| 融合 + `gte-rerank-v2` | **100.00%** | **100.00%** | 94.15% | 520 ms |

“融合 + 重排”完整链路是向量召回 + BM25 式中文词法召回 + RRF 融合 + `gte-rerank-v2` 重排。重排带来约 0.3 个百分点的 Source Hit/Evidence Recall 提升，并消除了本轮企业集中的失败样本；代价是远程重排调用增加延迟。策略指标只评估检索，不代表模型生成答案准确率。

## 门禁规则

- `official_policy`：Source Hit@K ≥ 0.95、Evidence Recall@K ≥ 0.95、检索失败数为 0。
- `enterprise_rag`：使用 `data/evals/enterprise_regression_thresholds.json` 中的 regression 阈值。
- `dongshan_legacy`：只做兼容性参考，不阻断总体门禁。

只有完整在线运行才会输出 `quality_gate`；`--offline` 或 `--limit` 运行会标记为 `smoke_only`，避免把局部或离线结果当成正式结论。
