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
```

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

## RAG 评估

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
