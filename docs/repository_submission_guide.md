# GitHub 提交边界

本文说明本地运行文件与 GitHub 源码仓库的边界。`.gitignore` 只会阻止未跟踪文件进入提交；已经被 Git 跟踪的文件不会因为后来加入 ignore 就自动移出仓库。

## 不应提交

- `.env`、`.env.local`、`.env.development`、`.env.production` 以及任何真实 API Key；只提交 `.env.example`。
- Python 字节码和缓存：`__pycache__/`、`*.pyc`、`.pytest_cache/`、`.mypy_cache/`、`.ruff_cache/`。
- 前端依赖和构建产物：`frontend/node_modules/`、`frontend/dist/`、`*.tsbuildinfo`、`.vite/`。
- 本地运行数据：`data/memory.db`、SQLite WAL 文件、`data/chroma_db/`、`data/eval_indexes/`、`data/uploads/`、`data/traces/`。
- 本地浏览器验收截图和报告：`work/`、`playwright-report/`、`test-results/`、`blob-report/`。README 专用、已审查且仅含合成资料的界面截图存放于 `docs/assets/`，属于可提交文档资源，不包含原始运行报告。
- 覆盖率、临时文件和本地工具缓存。

这些内容要么可以重新生成，要么可能包含会话、上传文档、内部路径或密钥，不适合上传到公开仓库。

## 应提交

- `backend/`、`frontend/src/`、`scripts/`、`tests/` 和配置模板。
- `requirements.txt`、`requirements-e2e.txt`、`frontend/package.json`、`frontend/package-lock.json`。
- `data/knowledge_base.json`、`data/evals/`、`data/eval_corpus/`：它们是项目的可复现种子数据、题集和官方语料 provenance。
- `docs/`、`README.md`、`.env.example` 和必要的设计系统文件。

当前工作区中的 `design-system/` 和
`docs/frontend_optimization_implementation_plan.md` 按项目协作约定作为本地参考文件，
已加入 ignore，不会被普通的 `git add .` 上传；如果未来决定把它们公开，再移除对应 ignore
规则并单独审查其中的内部设计和实施信息。

当前 `data/eval_reports/` 中的报告已经被 Git 跟踪，并在 README 中作为评测证据引用，因此暂时保留。若后续希望改成“代码与数据集仓库、不提交生成报告”，需要先人工确认报告是否仍需发布，再执行：

```powershell
git rm --cached data/eval_reports/*.json
```

该命令不会删除本地报告，但会让下一次提交从 GitHub 移除它们；执行前应确认不影响项目文档和发布说明。

## 提交前检查

```powershell
git status --short --ignored
git check-ignore -v .env frontend/dist frontend/node_modules data/memory.db work
git diff --check
```

看到 `!!` 表示文件被忽略，看到 `??` 表示仍有未跟踪文件，需要确认是否属于应提交的源码或文档。


## 2026-09-28 同步范围与复现入口

本轮同步产品工作区与等待体验、日期/段落/稳定来源引用修复、DeepSeek 文本接入、Tavily 搜索、固定每页 10 份分页、论文式引用及相关测试/说明。提交源码、配置模板和测试用例；不上传本地 `.env`、原始 trace、SQLite/Chroma、上传资料、截图、依赖和构建产物。

公开仓库中的文档可能引用本地 `work/` 证据路径；它们用于说明当时的验证来源，不代表克隆仓库后自带这些报告。复现需按 [优化方案 M6 执行清单](product_experience_optimization_plan.md) 运行测试；真实链路依赖各供应商凭据，使用隔离数据且消耗配额。

GitHub 源码同步与部署、M6 全部验收是不同状态。当前 M1–M5 已实现，M6 尚有性能/成本/真机/兼容性及多版本质量待办；具体以 [当前验收报告](product_experience_final_acceptance.md) 为准。不能因为提交或推送成功，就将未验证项标为通过。
