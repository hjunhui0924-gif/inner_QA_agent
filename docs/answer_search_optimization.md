# 回答与搜索优化说明

2026-09-12：优先修复用户实际遇到的 RAG 漏引用、联网搜索空结果却判成功。保留现有 RAG 协议和校验，不增加完整 claims JSON 链路。

2026-09-13 的后续 [提示词完整性优化](prompt_completeness_optimization.md) 调整了回答范围、必要要点覆盖与逐项引用，下文数字保留为该轮优化前的基线。

## 模型选择

使用同一组审批规则、材料清单、无答案问题比较本地可用模型。OCR 模型出现漏引用和无答案误答；`qwen3.7-plus` 调用失败，未获得可比较答案；`qwen3.8-flash` 三题满足预期。证据在 `work/backend-verification/model-comparison.json`。这只是当前账户和任务上的小样本，不是模型能力排名。

默认及本地 `MODEL_NAME`、`JUDGE_MODEL_NAME` 改为 `qwen3.8-flash`，保留独立配置和现有凭证。知识库答案仍经过引用检查与语义检查，校验失败时提示已找到资料但未能生成可验证回答；资料不足则明确提示补充资料。通用模式使用单独提示词，避免继承知识库模式的企业资料限制。

## 联网链路

按 [DashScope 官方联网搜索文档](https://help.aliyun.com/zh/model-studio/web-search) 接入原生搜索。当前账户实测 `qwen3.8-flash` 的 multimodal-generation SSE 可返回答案、`search_info` 与引用；另一个 text-generation 探测因免费额度策略被拒，没有依赖该路径。

```env
WEB_SEARCH_MODEL=qwen3.8-flash
WEB_SEARCH_ENDPOINT=https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation
WEB_SEARCH_TIMEOUT_SECONDS=20
```

一次原生搜索已生成带来源的答案，后端直接复用，避免二次生成及额外 Judge 调用。图使用可取消的异步 HTTP；同步入口保留给公共工具。模型、工具调用和已知 Token 用量都计入预算，包括上游失败事件中已报告的用量。费用估算仍依赖配置的单价，并非供应商账单。

校验要求答案非空、不是 JSON 或查询回显、所有引用编号对应有效 HTTP(S) 来源；截断、不完整流、超过字节上限及非法来源均拒绝交付。最多收集 100 条来源，最终保留实际引用的最多 10 条。稳定错误码区分无结果、服务故障和答案无效，前端展示对应提示。

网页证据包含标题、URL 和 `verification_status=web_source`，不把模型答案当作逐字原文。前端显示“搜索服务提供的来源”和安全链接；没有原文就不显示原文复制。引用 URL 随消息持久化，可在刷新后恢复。

这条链路验证的是搜索服务的来源指针，没有独立抓取全文核对每句事实。内部知识库的原文证据与网页来源采用不同标签，避免夸大验证程度。

## 验收与边界

- 后端离线回归：208 passed、2 skipped；前端：70 passed，生产构建通过。
- 无模拟的真实前后端联调：14/14 通过，覆盖上传、RAG、追问、无答案、搜索、来源恢复和异常接口。
- 34 题开发评测：自动 Judge 四项通过 33/34，引用出处 100%，引用完整性 98.33%，无答案拒答 4/4。
- 剩余问题：一题遗漏“及时告知用户”，一题开头结论句缺独立引用；严格短语匹配另保留失败。生成与 Judge 同模型，不是独立人工评判，也未替代三套数据的统一质量门禁。

独立代码复核发现并修复了失败用量丢失、第 11 条来源被错误截除、单行 SSE 缓冲超限三个问题，新增确定性回归后复核通过。完整证据路径与测试范围见 [后端与前端真实接口验证](backend_frontend_verification.md)。
