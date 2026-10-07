# 内部知识助手业务场景验收记录

日期：2026-10-07。本轮按用户选择先验收现有资料，再补充官方公开文件。现有资料 30 题与新增公开资料 26 题均完成真实 Agent 运行、预检查及 Codex 对原文的逐条核对。发现并修复了两处多结论引用摘录遗漏，以及一处明确比较两机构文件时的关键条款漏检。本次后续复测又修复了公开长文的跨页摘录上下文，并用 PB09 原题和新增跨页金额题完成隔离真实 Agent 复测。

这是本轮样本的结果，不是全量业务准确率、独立专家验收或生产可用性保证。生成与 Judge 使用同一个模型，Codex 也参与了题集编制和修复，审核不独立。

## 资料和题集

| 范围 | 资料 | 题目 | 重点 |
|---|---|---|---|
| 现有知识库 | 49 份合成制度 + 1 份上传的公开法律意见书 | BQ01–BQ30，共 30 题 | 八部门流程、现行/旧版、连续追问、金额边界、多来源、3 次拒答 |
| 公开文件扩展 | 原有 50 份 + 5 份官方公开 PDF，共 55 份记录；新增 PDF 共 73 页 | PB01–PB26，共 26 题 | 真实长文和附表、三万元与三个月边界、365/366 天、机构规则差异、缺失金额拒答 |

两组共有 56 个题目编号。连续追问、同一原文的关联问题不具备统计独立性；失败批次与 PB10 定向复测也不能累加为新的独立题目。

新增资料来自南京大学、西南政法大学、华东师范大学、浙江大学和 Tenaris 泰纳瑞斯的官方站点。保留了文件签署日期、文号、下载时间、官方 URL、PDF 与提取文本 SHA-256，详见[公开资料题集及来源](public_business_acceptance_cases.md)和[来源清单](../tests/fixtures/public_business_sources.json)。2018 年文件按文件快照测试，未证明仍为现行制度；泰纳瑞斯文件未注明年份，不推断其年份。

现有题集详见[业务场景验收表](business_quality_acceptance_cases.md)。日常 `data/knowledge_base.json` 的原始 SHA-256 为 `0568df31708a94dff58628ac89acb766f4b9d8db91e0a322b4f83c9a0c30c34e`，本轮不修改日常知识库。

## 执行方式和证据

题目、原文依据与语料在每组首次模型调用前冻结。实时运行使用自己的知识 JSON、数据库、向量索引、上传目录和 8117 端口服务，结束后关闭所创建的服务。日常会话、上传文件、`.env` 及既有前端服务不参与隔离存储的写入。

配置为 DeepSeek `deepseek-v4-pro` 生成及 Judge，`CITATION_VALIDATION_MODE=judge`，`qwen3.7-text-embedding`，`gte-rerank-v2`，`retrieval_strategy=rerank`，Top K=4，单请求总预算 60 秒。模型、embedding 和 rerank 使用真实服务，问答阶段 `web_search=false`。Tavily 只用于前期寻找公开文件，回答不能靠实时网页搜索绕过知识检索。

两组使用普通 `employee,knowledge_reader` 模拟身份、八个部门声明和 `internal` scope，没有管理员角色；现有语料 ACL 保留。此设置便于覆盖本轮题目，不代表已完成真实组织的人员授权验收。历史无权来源专项与本轮质量样本分开统计。

每个运行目录保留 `manifest.json`、冻结题集、冻结语料、源码差异及哈希、原始回答、会话历史和日志。原始 `report.json` 的 `semantic_review=pending` 不反向改写；逐题审核结论另存 `codex-review.json`，并绑定原报告哈希。

| 批次目录，均位于 `work/business-quality/` | 原始预检查 | 后续审核 / 复测 |
|---|---|---|
| `20261007-initial/` | 27/30 | 三题允许来源遗漏；修订来源清单重放后，BQ15/BQ20 的摘录仍失败，证明没有用题集修订掩盖缺陷 |
| `20261007-final/` | 29/30 | BQ25“安全负责人审批”被第二版关键词误报；第三版等价表述重放 30/30，Codex 原文核对 30/30 |
| `20261007-public-initial/` | 24/26 | PB06 额外检查未询问的特殊审批；修订重放 25/26，Codex 核对仍保留 PB10 的实际漏检 |
| `20261007-public-final/` | 26/26 | 修复比较检索后完整组复测，Codex 原文核对 26/26；PB09 的摘录可读性单独保留 |
| `20261007-public-scope-regression/` | 1/1 | 在最终代码补充年份/版本保护后，仅重测受影响的 PB10，完整回答与两份引用再次核对通过；这是重复题 |

现有 30 题的运行发生在比较检索修复之前；它们不使用本次新增的明确两机构比较形式，该分支不改变这些题目的检索输入。公开 26 题完整复测后补充年份/版本保护，只对 PB10 进行了最终定向真实 Agent 复测；不把各批次描述成同一次运行。

## 发现与修复

### 多结论引用摘录遗漏

BQ15 的回答同时包含“无采购订单不得付款”和“三单不一致暂停付款”，初始 C1 摘录只展示前者。BQ20 同时回答预订审批和风险报备，初始摘录遗漏紧急联系人与行程报备。原文和正确检索块均已存在，问题发生在引用摘录选择。

`backend/agent/citations.py` 对同一标记对应多项结论的情况，优先比较各项结论的最低和平均词元覆盖，并增加字符预算内的连续宽窗口。原文连续性和长度上限保留，单结论沿用原排序；没有放宽来源校验、语义 Judge 或无引用安全门禁。

`tests/test_citations.py` 增加两项真实失败模式的回归测试。两项测试在修复前失败，修复后通过；题集另加入关键摘录断言。BQ15/BQ20 在 30 题真实复测中均展示了两项结论所需的原文。

### 明确比较两机构时的条款漏检

PB10 要求比较两校邀请专家的城市间交通费规定。初始回答正确给出了南京大学规则，但声称西南政法大学文件未包含直接规定，实际第 25 条规定“一般不报销；确需报销由经费负责人和分管校领导审批”。

真实检索探针确认该条款已在 12 个重排候选中，却被机构名称、年份匹配度较高的介绍页挤出前四。按具体业务问题重排的探针找回了该条款。

`backend/retrieval/engine.py` 对明确“比较两份文件”的问题保留完整问题进行向量与词法检索；只有授权过滤后的候选中能识别两个明确提及的 `owner` 时，才限定这两个机构并按具体业务问题重排。明确的文件年份继续限定候选；无法确认机构/年份或存在 `v2` 等复杂版本表达时，沿用完整问题。

该处理没有追加模型调用或扩大 Top K，也不恢复被 ACL 排除的文档。`tests/test_comparison_retrieval.py` 覆盖双方关键条款、其他机构排除、不完整机构匹配、ACL、文件年份及紧邻中文名称的版本表达。范围有限，不宣称任意自然语言多文档比较都已解决。

### 题集修订与真实缺陷分开

- BQ15/BQ17/BQ25 的补充来源经原文核对有效；主来源和问题事实保留。
- BQ25 第三版只增加原文支持的“安全负责人审批”等价关键词；保留第二版原始 29/30 报告。
- PB06 只删除题目未询问的“特殊情况审批”必需关键词，仍要求 60% 和 10 万元及正确来源。
- PB10 的问题、业务事实、必需来源没有放宽；失败报告、修复前探针和完整复测均保留。

## 回归与复跑

最终后端回归：**302 passed、2 skipped**，最新日志为 `work/citation-context/20261007/backend-regression-final-4.log`。新增跨页回归与原有引用、知识入库测试共 61 项先行通过；两个跳过项是既有按环境开关启用的在线契约，本轮全后端命令没有启用它们。最终代码下 PB01/PB06 的原有差旅题隔离复测为 **2/2**，PB09 与新增 CX01 跨页题复测为 **2/2**，分别记录在 `work/citation-context/20261007/nju-live-final/report.json` 和 `work/citation-context/20261007/context-live-final-2/report.json`；不把长批次中服务调用失败的 29 题运行结果冒充成完整通过。

```powershell
python -m pytest tests -q
python scripts/run_knowledge_quality_audit.py --validate-only --cases tests/fixtures/business_quality_acceptance.json
python scripts/run_knowledge_quality_audit.py --validate-only --cases tests/fixtures/public_business_acceptance.json

# 输出目录必须尚不存在；实时运行会消耗服务配额。
python scripts/run_knowledge_quality_audit.py --live --cases tests/fixtures/business_quality_acceptance.json --output-dir work/business-quality/existing-new-run --port 8117
python scripts/run_knowledge_quality_audit.py --live --cases tests/fixtures/public_business_acceptance.json --output-dir work/business-quality/public-new-run --port 8117
```

本轮未改前端，因此没有用此前的浏览器、构建或前端测试数量充当本轮后端修复证据。语料/题目哈希预检查、Python 语法、文档链接及差异空白检查完成后，结果同步到[项目当前状态](current_status.md)。

## 本轮剩余边界

PB09 原回答曾出现“政局发布的标准……”的跨页续句摘录。现在分块重叠会在有界范围内回退到前一个分句边界；新增单测覆盖 PDF 软换行、80 元规则及跨页 40/60 元规则，PB09 与 CX01 的隔离真实 Agent 运行均保留了适用条件。29 题组合复测中第 8 题后出现服务调用回退，不能替代逐题完整复测。

公开文件使用预提取文本记录，新增 5 份 PDF 的上传/自动解析/原页预览链路没有在本轮重新验收；表格按相关原页核对，未覆盖所有表格和扫描件。不同机构资料用于测试规则区分，不进入日常企业知识库。

下一阶段仍需真实授权企业制度、实际部门权限身份、未参与修复的留出问题及独立业务审核。真机软键盘、同配置可比性能基线、长期稳定性与可归因账单成本沿用既有待办，本轮不补足这些证据。
