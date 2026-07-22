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
| 向量检索 | 100.00% | 91.78% | 86.67% | 86.67% | 284 ms |
| BM25 式词法检索 | 96.67% | 92.78% | 86.67% | 86.67% | 1.4 ms |
| BM25 + 向量 + RRF | 100.00% | 93.89% | 96.67% | 96.67% | 204 ms |
| BM25 + 向量 + RRF + Rerank | 100.00% | 95.00% | 100.00% | 100.00% | 726 ms |

完整报告位于 `data/eval_reports/official_policy_retrieval_benchmark.json`。Rerank
分数在可回答与无答案样本之间仍有重叠，因此项目没有根据这 34 条数据硬编码拒答阈值；
无答案识别需要独立校准集和证据充分性判别，不能由 Top-K 命中率替代。

## RAG 回答评估

项目内提供了一版最小可用的 RAG 评估脚本，当前针对“东山精密：2025年度股东会法律意见书”准备了标准评估样本，用于验证企业文档在检索层和最终回答层的表现。

评估文件：

- `data/evals/dongshan_legal_opinion_eval.json`

运行方式：

```bash
python scripts/run_rag_eval.py
```

当前评估覆盖两层：

1. 检索层评估

- 检索是否命中目标文档
- 检索上下文是否覆盖标准关键词

2. 最终答案评估

- 最终答案是否覆盖标准关键词
- 最终答案是否包含标准答案或核心片段
- 最终答案是否忠于检索上下文

当前报告中的核心指标包括：

- `hit_at_k_rate`
- `average_keyword_match_ratio`
- `average_answer_keyword_match_ratio`
- `answer_contains_gold_rate`

评估结果会输出到：

- `data/eval_reports/dongshan_legal_opinion_eval_report.json`

### 当前样本结果

当前针对“东山精密：2025年度股东会法律意见书”的一版评估结果如下：

- `question_count = 8`
- `hit_at_k_rate = 1.0`
- `average_keyword_match_ratio = 0.6875`
- `average_answer_keyword_match_ratio = 0.7083`
- `answer_contains_gold_rate = 0.375`

### 结果解读

从当前结果可以看出：

- 检索层表现较好：目标文档能够稳定命中
- 回答层仍有优化空间：模型在部分问题上会偏离原文，尤其是数字类和决议性质类问题

当前暴露出的典型问题包括：

- 对数字类问题，模型可能尝试自行推算，而不是直接复述原文
- 对法律/制度类问题，模型可能会补充泛化解释，而不是严格依据检索内容作答

这类评估结果可以直接用于后续优化：

- 调整 `chunk_size`、`top_k` 和检索策略
- 改进 PDF 文本抽取质量
- 收紧生成提示词，减少超出证据范围的推断

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
