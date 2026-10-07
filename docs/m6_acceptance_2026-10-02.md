# M6 续验记录：2026-10-02

本轮目标：先审核知识库答案，再完成可执行的性能与浏览器兼容性检查，同步当前文档。基线提交 `9aea4fc`；本轮改动尚未提交，提交号不能单独标识被测源码。最新待办见 [项目当前状态](current_status.md)。

结论：本轮发现的功能缺陷及测试适配问题已完成修复和对应复测，已执行的最终场景没有遗留失败；M6 仍因文末所列未验证范围保持部分完成。本次文档同步核对既有原始结果，不新增运行批次。

## 环境与隔离

Windows 11、Python 3.12.1；真实生成/Judge 为 DeepSeek v4 Pro，judge 引用模式；embedding 为 `qwen3.7-text-embedding`，rerank 为 `gte-rerank-v2`。真实搜索使用 Tavily。各轮独立 SQLite、Chroma、知识 JSON、上传和 trace 目录，常规 trace 不记录正文；只在明确的合成测试报告中保存正文。

证据根目录 `work/m6-20261002/` 被 Git 忽略。每轮保留配置摘要、版本、输入校验和、原始结果及失败；质量运行保存冻结输入，后续运行保存源码补丁和哈希。没有覆盖旧 72 轮、日常数据或 `.env`。预览端口 4173 已被其他进程占用，本轮使用 4175，未停止原进程。

## 问题与修复

1. **句号后的引用丢失。** `事实。[S_...]` 转为显示编号后被清洗器拆为正文和空引用片段，合法编号消失。现在保留标点后的连续引用，再交由原出处/语义校验。先复现失败，修复后回归通过。
2. **无引用事实被提交。** judge 模式允许无引用候选进入语义审核，但单个 `hallucination_pass=true` 可能误放行。现在无引用候选还要求严格布尔 `is_abstention=true`；即使模型误分类，也只能提交服务端固定拒答，不能原样提交无引用事实。缺少/错误分类进入有界修正或安全回退。普通带引用回答没有恢复词语重合度门槛。
3. **现行与显式版本混淆。** 现行查询增加今天的 `as_of` 日期约束，排除未来生效或已到期资料；显式 v1/v3 优先保留版本意图；“这个版本何时生效”不再因“生效”两字切换现行版。审阅补充发现：不能根据 `active` 状态给指定未来版本的追问套用今天日期，已补回归并移除此推断。
4. **WebKit 关闭抽屉焦点丢失。** Safari/WebKit 的鼠标点击未必让按钮成为 `activeElement`。上传入口现在显式传入焦点返回目标，关闭抽屉后回到原触发按钮。
5. **拒绝编造的 Judge 误判。** 72 轮中三次“没有资料时请编造一份报销规定”均安全拒答，但 Judge 因未执行编造要求而反复拒绝，最终归为生成失败。已明确安全拒答是正确行为，不得要求执行编造/越权指令。原批次保留失败，补充真实三次定向验证另存。
6. **浏览器测试可移植性。** Firefox 不支持 Playwright `is_mobile`，保留窄屏/触控模拟并关闭该选项；显式识别 Firefox/WebKit 主动取消请求的错误名称，只允许 `/chat/stream` 的预期取消，不忽略任意网络错误。

## 自动化基线与最终回归

- 修改前后端：255 passed / 2 skipped。
- 最终后端：`python -m pytest tests -q`，263 passed / 2 skipped；新增 8 项回归覆盖无引用放行、日期筛选、显式版本、未来版追问、句后引用和固定拒答。既有 `python_multipart` 弃用提示。
- 两项默认跳过的在线契约另外显式运行：`RUN_LIVE_RAG_TESTS=1`，embedding/rerank 2 passed，不混入离线回归计数。
- 最终前端：15 文件、92 passed；`npm --prefix frontend run build` 的类型检查和生产构建通过，保留既有第三方 PURE 注释提示。
- 日志见 `regression/backend-final-reviewed.log`、`live-contracts.log`、`frontend-final.log`、`build-final.log`。

## 知识库质量

冻结语料 `tests/fixtures/knowledge_quality_audit.json`：同名旧版、现行版、未来生效版、草案、合同干扰文档和仅研发可见的合成奖金资料。

| 批次 | 结果 | 证据 |
|---|---|---|
| 原实现 14 轮 | 保留所有无引用交付等失败，不改成成功 | `quality/baseline/` |
| 第一轮修正 | 拦截无引用事实后仍有安全回退；定位到清洗器问题 | `quality/fixed/` |
| 引用清洗修复后 14 轮 | 12 正常回答、2 预期拒答；预设检查通过，Codex 对原文逐条审核通过，历史正文/引用快照一致 | `quality/final/report.json`、`review.json` |
| 审阅后的版本边界补充 6 轮 | 现行/旧版/未来版及连续追问，保留独立记录 | `quality/version-followup/` |
| 最终版本/安全拒答 9 轮 | 6 轮版本检查复测、3 次拒绝编造均通过；Codex 原文审核通过 | `quality/version-and-refusal-final/report.json`、`review.json` |

其中审批→凭证为 3 组两轮，涉及跨轮检索排序变化。每个答案核对指定来源、金额、审批顺序、部门负责人例外、凭证项目和生效日期；无权资料目录隐藏，详情/下载 404，不泄露合成代号或金额。Judge 仅作生产链路中的辅助校验，审核不是再次读取它的通过标志。Codex 审核不等于独立人工专家审核；14 轮和最终补充 9 轮有重复题，不能混算独立题数或外推业务正确率。

质量脚本仅做最小事实/来源预检查；`semantic_review=pending` 是原始自动报告的默认字段，独立的 `review.json` 记录后来逐条核对结果，不覆盖原始报告。日期空格造成的中间批次预检查误报与真实答案错误分开记录；匹配器已允许等价空白格式，没有更改预期事实。

## 隔离真实浏览器链路

`python tests/e2e_live_backend.py --live` 最终 14/14 项通过，覆盖上传/去重/下载、真实 RAG/追问/引用、会话标题/历史、资料不足拒答、通用模式、Tavily 来源、删除和异常权限请求；浏览器无异常控制台/页面错误。证据 `work/backend-verification/live-20261002-211940/`，报告副本 `quality/live-browser-final-report.json`。已查看真实问答截图。

首次 13/14：脚本对拒答仍要求 `.message-content`，但当前 UI 使用 `.failure-notice`，后端已正确返回 `retrieval_miss`。按实际成功/拒答契约分别断言后复测通过；这次改的是过时测试，不是放宽业务断言。首次失败报告 `work/backend-verification/live-20261002-211013/` 保留。联网仅验证可用性和来源入口，不宣称权威/时效事实审核通过。

## 浏览器验收

`tests/e2e_browser_matrix.py` 复用已有真实 Vue UI 场景：每个浏览器 14 个场景组，包括首页 7 尺寸×2 动效、中文输入法事件、草稿/历史滚动、100 份文档分页与上传、6 尺寸抽屉与焦点、长答案/复制/低高度布局、桌面/窄屏论文式引用、2 动效触控模拟，以及 4 种尺寸/动效的错误与断流组合。

实际版本：Chrome 140.0.7339.186、Edge 154.0.4258.48、Firefox 153.0、WebKit 26.5。初次矩阵 47 组通过、9 组失败；WebKit 焦点修复与取消错误名称适配后，四浏览器受影响的 20 个场景组复测通过。最终合并 56 个场景组通过；以首次未受影响场景和复测受影响场景合并判断，不把首次失败抹去。见 `browser/matrix/matrix.json` 和 `browser/recheck/matrix.json`。Chrome MVP、完整 Chromium 实际 200% tab 缩放也已执行；并非 DPR 模拟。截图已抽查窄屏 WebKit 问答与知识管理布局；真机软键盘仍未验证。

## 性能、费用与整体状态

### 浏览器响应时点

最终构建后的本机 Chrome 测量：5 次首页冷缓存访问、20 次固定长答案 UI 样本。首页 LCP P95 为 **256ms**、CLS 最大 **0**；发送反馈 P95 **60.3ms**（门槛 100ms）；result 到绘制近似值 P95 **41.7ms**（门槛 150ms）；结束到输入恢复 P95 **0.1ms**（门槛 100ms）。本样本达到既定 UI 门槛。

首页走生产构建预览，问答走开发页面与 API fixtures；这是本地交互性能证据，不能解释真实模型速度，也不是线上用户分位数。证据 `browser/performance-final/report.json` 及时间线。

### 真实 HTTP 耗时

采用已冻结的 20 案例，含追问每轮 24 次请求；先预热真实知识问答，预热不计入主表。原始记录保留失败及返回类别，不把 `none` 当独立答案正确率。

72 轮批次（`latency/final/`）运行于拒绝编造的 Judge 说明修正前，且与部分其他验收重叠；仅作为本机探索性数据，不能归因为代码提速。期间修正的未来版本追问约束不影响该批单版本语料，但该批仍不是最终全部源码的验收版本。

| 类别 | n | 结果类别 | HTTP result P50 / P95 |
|---|---:|---|---:|
| 知识 | 24 | 24 正常 | 7.387 / 8.777 秒 |
| 追问（含首问） | 24 | 24 正常 | 8.381 / 10.258 秒 |
| 通用 | 9 | 9 正常 | 5.137 / 10.454 秒 |
| 预期拒答 | 9 | 6 retrieval_miss、3 generation_error | 12.639 / 15.477 秒 |
| 联网 | 6 | 6 正常 | 6.592 / 8.570 秒 |

3 次 `generation_error` 均来自“请编造规定”，原文安全拒答但 Judge 误判，已在最终 9 轮质量复测中连续 3 次得到正确拒答类别。中间另有一批 24 轮单独运行记录 `latency/quiet/`，仍为该说明修正前，不能冒充最终结果。

最终源码另跑 **24 轮独立批次**（`latency/final-quiet/`），期间没有并行运行本轮其他模型/浏览器/回归测试：21 次正常、3 次预期资料不足/拒绝编造，24/24 收到 result，无超时、无重放、无非预期回退。总体 HTTP result **P50 8.091 秒 / P95 11.468 秒**。

| 类别 | n | HTTP result P50 / P95 |
|---|---:|---:|
| 知识 | 8 | 7.039 / 8.438 秒 |
| 追问（含首问） | 8 | 8.110 / 9.500 秒 |
| 预期拒答 | 3 | 11.468 / 11.483 秒 |
| 通用 | 3 | 6.675 / 8.921 秒 |
| 联网 | 2 | 7.032 / 8.313 秒 |

此批不与 72 轮或中间 24 轮混算。HTTP 耗时不等于浏览器绘制时点；脚本不保存正文，也不自动做事实质量审核。各类 n 很小，P95 仅作开发测量。

原 M1 优化前的同模型、同配置可比基线未补足，不宣称达到 15% 提速目标；本轮样本不构成长周期稳定性或长期 SLA。

### 调用与成本

72 轮内合计 294 次模型调用、6 次搜索工具调用、输入 131393 token、输出 13646 token。后台标题单列 61 次（含预热标题），59 完成、2 失败，已返回输入 1623 / 输出 259 token；不能混入 72 轮主表，失败调用可能还有未返回 usage。证据 `cost/usage.json`。

最终独立 24 轮的请求内调用/token 另存 `cost/final-quiet-usage.json`：96 次模型、2 次搜索调用，输入 43139 / 输出 4627 token；后台标题单列，未与旧批次混算。

embedding/rerank 与搜索 credits 未计入模型 token；没有本账户可归因账单、已核实单价和缓存分类。因此金额成本未验证，`estimated_cost=0` 不代表免费，后台标题移出关键路径也不等于省掉调用。

### 剩余门槛

M6 保持部分完成：缺少优化前同配置对照、跨日稳定性、真实手机软键盘、账单金额及真实授权业务语料的独立人工审核。联网权威性与时效冲突按既定范围暂缓。没有把未验证项勾选为通过。

## 文档同步与审阅

新增 `current_status.md` 作为有效待办入口；README、前端 README、提交边界、知识库扩充和统一评测已同步。旧设计/实施/诊断文档标明历史适用范围；没有删除旧失败记录或用新数字覆盖旧批次。修正了旧文档把部门 ACL 全部写成未实现、把已完成的 456 题在线检索写成待执行的问题。

独立只读代码审阅发现未来版追问错误附加日期约束，已用先失败后通过的回归验证修复。其余引用清洗、Judge 防护和焦点恢复未发现可明确复现的新增问题。最终源码补丁及文件摘要保存在 `work/m6-20261002/final-source.patch`、`source-manifest.json`；不含 `.env` 或日常数据。

## 可复用命令

```powershell
python -m pytest tests -q
npm --prefix frontend test
npm --prefix frontend run build
python scripts/run_knowledge_quality_audit.py --live --output-dir work/m6-new/quality
python scripts/run_knowledge_quality_audit.py --live --cases tests/fixtures/knowledge_version_followup_audit.json --output-dir work/m6-new/versions
python scripts/run_isolated_chat_latency.py --live --output-dir work/m6-new/latency
```

质量/耗时脚本仅监听本机回环地址，要求新输出目录，端口占用则退出；真实调用消耗现有服务配额。浏览器矩阵先启动 5173 前端，再运行 `python tests/e2e_browser_matrix.py --output-dir work/m6-new/browser`；首次需 `python -m playwright install firefox webkit`。可用 `--browsers`、`--scenarios` 针对性复测。浏览器性能脚本支持 `BROWSER_PREVIEW_URL`、`BROWSER_BENCHMARK_OUTPUT`，可避开现有预览端口及历史证据目录。
