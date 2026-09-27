# Tavily 搜索接入（2026-09-27）

本地 `.env` 已使用已有的 `TAVILY_API_KEY`，设置 `WEB_SEARCH_PROVIDER=tavily`。
后端启动时读取配置，已经运行的后端需要重启。仓库默认仍为 `dashscope`，
其他部署可以显式选择 provider。密钥不写入文档或版本控制。

## 流程

Tavily basic 搜索（最多 5 条，不请求生成答案或完整网页）→ 当前文本模型
（本地为 DeepSeek `deepseek-v4-pro`）生成回答 → 校验 `[Cn]` 是否对应检索来源。
输出沿用现有工具和前端引用契约，无需修改前端。来源映射校验不等于独立事实核查。

两个服务使用各自的凭据。资料按不可信数据传给模型，过滤无效 URL、空摘要及重复来源；
限制响应体、摘要和答案长度。不向其他 provider 自动重试，不额外消耗搜索额度。
空结果、HTTP 错误、答案截断和不存在的引用会返回现有安全失败状态。
图流程的总超时覆盖检索和生成两阶段，用户取消会向下传播。
一次搜索仍预留 1 次工具和 1 次模型预算，记录模型返回的 token usage。
Tavily credits 不计入现有模型 token 成本统计。

同步工具入口保留兼容；和原有原生搜索入口类似，同步 HTTP 读取采用每次读取超时，
并在数据块到达时检查总截止时间，不能严格中断已阻塞的读取。前端使用的异步图流程
有严格的外层总超时，不受此限制影响。

## 验证

- `python -m pytest tests/test_tavily_web_search.py tests/test_native_web_search.py -q`：18 passed。
- `python -m pytest tests -q`：245 passed、2 skipped；1 项既有依赖弃用警告。
- 真实 Tavily + DeepSeek 调用：成功，约 5.03 秒，有引用和 token usage。
- 真实 `tool_executor → generate → check_hallucination` 节点调用：成功，约 6.50 秒，
  有来源引用，计入 1 次模型 / 1 次工具调用。使用官方文档域名限定查询。
- 独立代码审查：异步主流程未发现重要缺陷，同步超时限制如上。

耗时是单次样本，不是性能基准；本次没有浏览器端复测，也没有确认账户剩余额度。
