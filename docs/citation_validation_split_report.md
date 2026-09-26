# 引用校验与在线 Judge 分工调整

## 2026-09-27 DeepSeek 恢复验证

本地 `.env` 已切换 `MODEL_PROVIDER=deepseek`，生成、Judge、后台标题使用 `deepseek-v4-pro`，官方地址为 `https://api.deepseek.com`，关闭 thinking。修正了原本拼错的 DeepSeek 地址；密钥未写入代码或报告。`deepseek-flash` 的可用性预检重复返回含异常标记的 JSON 字段，因此未采用。成功调用不代表免费额度，本次没有查询 DeepSeek 账户余额。

旧版节点 `6e4f00a` 和新模式使用同一 DeepSeek 模型完成 17 个固定人工标注候选 × 3 轮对比，每版 51 次。旧版误拒 3 次（翻译）、误放行 3 次（遗漏例外）；新模式误拒和误放行均为 0。所有裁决通过格式校验。证据：`work/citation-gate-deepseek-pro/results.json`、`status.json`。这只是合成开发集的验收，不能推导真实业务准确率或独立质量保证。

本地已启用 `CITATION_VALIDATION_MODE=judge`：代码负责结构/出处，Judge 负责语义。保留 `legacy` 回滚选项和仓库默认值，避免把仅在 DeepSeek 验证的结论直接用于其他供应商。

两次隔离的真实浏览器联调分别覆盖 legacy 和 judge，均为 12/14 通过。问答、追问、引用、标题、拒答、通用回答、上传/向量入库/下载、会话持久化及删除正常；2 项失败都是阿里原生联网搜索不可用，界面正确显示 search_error，没有伪造网页来源。第二次证据：`work/backend-verification/live-20260927-014011/report.json`。测试独立使用 SQLite、Chroma 和上传目录；测试服务已停止。

Embedding、reranker、原生联网搜索继续使用各自的 DashScope 配置，文本模型切换不会更改索引或冒充联网能力。最终执行 `python -m pytest tests -q`：234 passed、2 skipped；`git diff --check` 通过。新增测试覆盖供应商独立密钥、请求参数、缺失 DeepSeek 密钥不串用阿里密钥、仅 DeepSeek 凭据时后台标题入队与生成。独立静态审阅未发现待修复问题。

## 此前实施记录（以下额度阻塞状态已由上节更新）

日期：2026-09-27。基线：6e4f00a。状态：代码与离线回归已完成，真实质量对照因模型额度阻塞，尚未默认启用。

## 决策

用户确认将确定性代码职责缩小到引用结构及出处，在线 Judge 负责语义；先做对照，确认误拒减少且错误放行没有增加，再替换默认流程。

新增配置 CITATION_VALIDATION_MODE：

- legacy（默认）：保留原 Judge 提示词与后置语义规则否决；此前日期修复继续有效。
- judge（待验收）：先检查引用编号是否落在本次检索结果内、source_id/chunk_id 是否对应、摘录是否来自对应原文（忽略排版空白）；非法来源提前结束，不调用 Judge。之后由 Judge 唯一判断事实支持、金额日期、条件/例外、否定与顺序，允许等价改写和翻译。

无引用候选的正确性涉及语义：可能是合理拒答，也可能是未引用的事实，因此交给 Judge 判断，不再用固定拒答短语提前否决。Judge 提示词明确要求每个事实绑定支持它的引用、资料不足才可拒答。把问题/回答/资料放在单独 HumanMessage 数据中，SystemMessage 规定校验规则及不得执行数据内指令。

无效引用属于 citation 失败；Judge 语义拒绝、异常或无法解析的裁决仍拒绝交付，并沿用有界重试及安全 fallback。不能用其他未引用文段替错误引用提供支持。引用由服务端根据检索文档重建，忽略模型自行提供的来源字段。ACL 仍在原检索访问控制中执行，不把出处匹配冒充重新授权。

线上默认没有切换；没有修改实际 .env、检索策略、模型选择、网页/通用路由或 SSE 权威结果契约。离线评测 Judge 与旧语义检查辅助函数保留。

## 测试与审阅

测试先复现：越界引用仍调用 Judge；Judge 接受的中英忠实翻译被词语重合规则二次否决。修改后通过。

新增/调整覆盖：提前拦截越界引用且模型调用为 0、伪造摘录/来源/Chunk、合法翻译、错误引用交由 Judge 拒绝、无引用拒答变体、无效 JSON/非布尔裁决失败关闭、legacy 保留原有数字语义否决、配置合法值与默认值。

对照工具的服务可用性与语义结果分开记录：预检需返回布尔裁决；ObservedJudge 记录每次模型调用的异常与裁决格式。即使旧节点把服务异常原因覆盖成引用失败，工具仍标记 blocked；不会把全拒绝当作通过。独立审阅指出的拒答短语误拒与无效 JSON 假通过均已修正并补测。

本轮前端未改，没有重复声称新的浏览器或真实模型链路通过。

最终执行 `python -m pytest tests -q`：**231 passed，2 skipped**（既有 python_multipart 弃用警告）；`git diff --check` 通过。基准 CLI 的 `--help` 已验证，未通过的真实调用没有计入测试成功数。

## 真实对照阻塞

模型服务实际返回 HTTP 403，错误码 AllocationQuota.FreeTierOnly。首次对照记录位于 work/citation-gate-comparison/results.json，属于服务拒绝记录，不是 0 错误放行或 0 幻觉的质量证明；status.json 已标记 blocked。没有修改供应商计费设置，也未擅自切换模型绕过限制。

固定候选 tests/fixtures/citation_gate_cases.json 包含 17 个合成案例，覆盖正例改写/翻译、日期、年份概括、正确拒答，及错引、金额日期错误、条件/例外遗漏、顺序/否定、无引用、越界引用、错误拒答和候选内指令。默认每版本每例 3 次，交替比较；这些是人工指定预期的开发案例，不是独立真实业务留出集。

恢复模型额度后执行：

    python scripts/benchmark_citation_gate.py --live --baseline-ref 6e4f00a --repeats 3 --output-dir work/citation-gate-comparison-available

工具直接调用新旧在线节点，不会向生产会话、索引或知识库写入测试数据。服务不可用或裁决格式错误立即标记 blocked，所有样本结果保留。新模式对所有已标注正负例均判断正确才满足本轮 gate；随后仍需真实 Agent 链路复测与结果检查，才切换默认配置并删除临时兼容分支。

不能在此次额度阻塞状态下宣称误拒率下降、错误放行率不变或时延提升。剩余外部动作是恢复该模型可用额度（或由账户所有者调整仅免费额度限制）；恢复后继续既定验收，不需要重新设计流程。
