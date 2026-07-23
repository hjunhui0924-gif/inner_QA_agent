# 企业内部知识助手

一个基于 `LangGraph + Qwen + FastAPI + Streamlit` 的企业内部知识问答项目，用于将企业制度、流程、合同、财务、人事等内部文档接入知识库，并通过 RAG 提供可检索、可追溯的问答能力。

## 项目简介

这个项目面向企业内部场景，核心目标是把企业内部文件沉淀为可检索知识库，并让员工通过对话方式查询制度、流程和规范。

当前能力包括：

- 企业内部知识问答
- 文件上传并写入知识库
- RAG 检索与回答校验
- 会话历史管理
- SSE 流式输出
- 知识库两层去重
  - 精确去重：内容指纹
  - 近似去重：文本相似度阈值

完整的项目建设顺序、技术选型依据、评测过程和失败闭环见
[《企业内部知识助手：从原型到可评测 RAG 系统的建设思路》](docs/企业内部知识助手_项目建设思路.md)。

## 技术栈

- `LangGraph`：Agent 工作流编排
- `LangChain`：消息、文档、工具与模型接口
- `Qwen / DashScope`：大模型调用
- `FastAPI`：后端接口
- `Streamlit`：前端页面
- `SQLite`：用户记忆、会话历史
- `Chroma`：向量检索库

## 目录结构

```text
backend/
  agent/
  api/
  config.py
  main.py
frontend/
  streamlit_app.py
data/
  knowledge_base.json
requirements.txt
README.md
```

## 环境要求

- Python 3.11+
- Conda 环境即可，不要求额外创建虚拟环境

## 安装依赖

```bash
pip install -r requirements.txt
```

## 环境变量

在项目根目录创建 `.env`：

```env
DASHSCOPE_API_KEY=你的DashScopeKey
MODEL_NAME=qwen3.6-plus
JUDGE_MODEL_NAME=qwen3.6-plus
QWEN_ENABLE_THINKING=false
EMBEDDING_PROVIDER=dashscope
EMBEDDING_MODEL=text-embedding-v3
EMBEDDING_DIMENSIONS=1024
EMBEDDING_INDEX_VERSION=v3
RETRIEVAL_STRATEGY=rerank
RETRIEVAL_DENSE_CANDIDATE_K=20
RETRIEVAL_LEXICAL_CANDIDATE_K=20
RETRIEVAL_RERANK_CANDIDATE_K=12
RETRIEVAL_DENSE_WEIGHT=0.5
RETRIEVAL_LEXICAL_WEIGHT=0.5
RERANKER_ENABLED=true
RERANKER_MODEL=gte-rerank-v2
RERANKER_API_STYLE=native
```

回答生成默认使用 `qwen3.6-plus`，并关闭思考模式，以提高抽取式回答和 JSON Judge 的指令稳定性。自动语义校验通过 `JUDGE_MODEL_NAME` 独立配置；当前同样设为 `qwen3.6-plus`，后续可以切换成不同模型做交叉评判。评测执行和指标计算全自动运行；现有 34 题保留为开发集，后续新增样本采用模型生成、原文包含校验和冲突样本自动剔除，尽量不引入逐题人工标注。

默认使用 DashScope `text-embedding-v3` 作为中文语义检索模型。Embedding
提供方、模型、维度或索引版本发生变化时，系统会自动使用新的 Chroma
collection，避免新旧向量混用。无网络的本地开发可显式设置
`EMBEDDING_PROVIDER=hashing`，但该模式仅提供词法检索能力，不应作为生产配置或
正式评测结果。

知识库去重不再使用语义向量：完全重复使用规范化内容指纹，近重复使用保守的字符
shingle 重合率。这样可以跳过格式略有差异的文件副本，同时保留主题相似但规则不同
的制度文档。

检索默认同时运行语义召回与 BM25 式中文词法召回，再通过 RRF 融合排序。词法召回
用于补足文号、金额、日期和制度原词等精确匹配场景，语义召回用于处理同义表达；
融合后的候选再通过 `gte-rerank-v2` 进行精排。远程精排超时或异常时自动降级为
RRF 结果，不中断问答。Rerank 模型与端点均可配置，模型说明以
[阿里云文本排序官方文档](https://help.aliyun.com/zh/model-studio/text-rerank-api)为准。

仓库中只保留 `.env.example`，不要提交真实 `.env`。

## 启动方式

启动后端：

```bash
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

启动前端：

```bash
streamlit run frontend/streamlit_app.py
```

## 主要接口

### 1. 对话流式接口

```http
POST /chat/stream
```

### 2. 上传知识文件

```http
POST /knowledge/upload
```

示例：

```bash
curl -X POST "http://localhost:8000/knowledge/upload" \
  -F "file=@./your_internal_doc.pdf" \
  -F "title=员工报销制度" \
  -F "source=finance_policy"
```

### 3. 查看知识条目

```http
GET /knowledge/records
```

### 4. 查看历史会话

```http
GET /chat/sessions/{user_id}
```

### 5. 删除历史会话

```http
DELETE /chat/session/{user_id}/{session_id}
```

## 官方文档检索 Benchmark

项目提供了一套可复现的多文档检索消融实验。语料来自以下官方原始页面：

- [中华人民共和国个人信息保护法](http://www.npc.gov.cn/npc/c2/c30834/202108/t20210820_313088.html)（中国人大网）
- [中华人民共和国数据安全法](http://www.npc.gov.cn/npc/c2/c30834/202106/t20210610_311888.html)（中国人大网）
- [生成式人工智能服务管理暂行办法](https://www.cac.gov.cn/2023-07/13/c_1690898327029107.htm)（国家互联网信息办公室）
- [网络数据安全管理条例](https://www.gov.cn/zhengce/content/202409/content_6977766.htm)（中国政府网）

下载器会校验最终域名、正文长度和人工固定的 SHA-256，并记录来源 URL 与抓取时间。
两个人大网页面当前只提供 HTTP，固定摘要可以发现内容变化，但不能替代 HTTPS
传输安全：

```bash
python scripts/download_eval_corpus.py
```

运行 BM25、向量、RRF、Rerank 四组消融实验：

```bash
python scripts/run_retrieval_benchmark.py --top-k 5
```

使用真实 DashScope 凭证验证 Embedding 与 Rerank 请求/响应契约：

```bash
# PowerShell
$env:RUN_LIVE_RAG_TESTS="1"
python -m unittest tests.test_live_contracts -v
```

当前 34 条评测包含 30 条可回答问题和 4 条无答案问题；四份法规主题高度相似，
用于检验相近条款、数字、日期、定义和跨境规则的区分能力。当前实测结果：

| 策略 | Source Hit@5 | MRR@5 | 证据召回@5 | 完整证据命中率 | P50 延迟 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 向量检索 | 100.00% | 91.78% | 86.67% | 86.67% | 245 ms |
| BM25 式词法检索 | 96.67% | 92.78% | 86.67% | 86.67% | 0.6 ms |
| BM25 + 向量 + RRF | 100.00% | 93.89% | 96.67% | 96.67% | 183 ms |
| BM25 + 向量 + RRF + Rerank | 100.00% | 95.00% | 100.00% | 100.00% | 671 ms |

完整报告位于 `data/eval_reports/official_policy_retrieval_benchmark.json`。Rerank
分数在可回答与无答案样本之间仍有重叠，因此项目没有根据这 34 条数据硬编码拒答阈值；
无答案识别需要独立校准集和证据充分性判别，不能由 Top-K 命中率替代。

## RAG 回答、引用与失败评估

答案层开发基准复用上面的 4 份权威法规和 34 个问题，其中 30 个可回答、4 个无答案。它运行 BM25 + 向量检索 + RRF + Rerank、一次 `qwen3.6-plus` 生成和一次自动 Judge，用来快速迭代生成与引用协议；不经过路由、查询改写、生产幻觉重试和安全 fallback，不能替代完整 Agent 的端到端验收。

- Gold Evidence Phrase 是否近逐字出现在回答中；
- 数字与日期是否匹配；
- 引用摘录是否逐字来自对应 Chunk；
- 回答中的事实句是否带有引用编号；
- 回答与引文的抽取式重合率（非蕴含判断）；
- 无答案问题是否出现明确拒答表达。

```bash
python scripts/run_answer_benchmark.py --top-k 5
```

完整运行会把报告写入 `data/eval_reports/official_policy_answer_benchmark.json`。这 34 题参与过提示词与规则迭代，属于开发集而非独立留出测试集。报告中的 Gold Phrase、数字、引用出处、引用完整性和抽取重合均为确定性代理指标，不应表述为人工验证的“回答准确率”或“忠实度”。只有 `verification_status=provenance_only` 的引用出处可以被确定性验证；语义支持需要独立 Judge/NLI 或人工标注集。

当前 `qwen3.6-plus` 完整自动评测结果：

| 指标 | 结果 | 样本数 |
| --- | ---: | ---: |
| 自动 Judge 回答正确 | 100.00% | 34/34 |
| 自动 Judge Grounded | 94.12% | 32/34 |
| 自动 Judge 引用支持 | 94.12% | 32/34 |
| 自动 Judge 拒答正确 | 100.00% | 34/34 |
| 自动 Judge 四项全部通过 | 94.12% | 32/34 |
| 数字/日期匹配 | 100.00% | 30 个可回答样本 |
| 引用出处准确率 | 100.00% | 30 个可回答样本 |
| 引用完整性 | 100.00% | 30 个可回答样本 |

剩余 2 个 Judge 失败样本的核心答案正确，但额外补充了问题未要求、且当前引用未覆盖的岗位资质说明。自动 Judge 与生成模型当前均为 `qwen3.6-plus`，因此这些语义指标应视为自动开发评测结果，而非独立人工结论。

仓库不提交凭证配额耗尽、样本数不足或中途失败的答案报告。模型调用失败记录为 `generation_error`；调用成功但保守代理没有匹配 Gold Phrase 时记录为 `gold_phrase_mismatch`，不会混为一类，也不会把安全拒答或回显的 Top-K 文档误算成正确答案。

旧版东山精密 8 题脚本仍保留为单文档回归检查，可运行 `python scripts/run_rag_eval.py`。其字符串包含指标不再作为正式回答准确率依据。

线上问答通过 SSE `result` 事件返回最终答案、结构化引用、Trace ID 和失败类型。引用包含文档 ID、文件名、页码/章节、Chunk ID 与逐字原文。明文 Trace 默认关闭；显式设置 `TRACE_ENABLED=true` 后才会写入受容量轮转保护、且已被 Git 忽略的 `data/traces/rag_traces.jsonl`。

## 支持上传的文件类型

- `.txt`
- `.md`
- `.csv`
- `.json`
- `.pdf`
- `.docx`
- `.py`
- `.log`

## 后续可扩展方向

- 更强的 PDF 表格抽取
- 更高质量的语义去重
- 文档版本管理
- 更细粒度的权限控制
- 前后端分离界面

## License

MIT
