# 产品体验收尾验收（更新至 2026-09-28）

> 当前状态：知识库追问的跨轮次编号干扰已修复，原现场重放（含对照）6/6、完整隔离问答 7/7 通过；该次后端回归 253 passed / 2 skipped，见 [稳定来源标识修复](stable_evidence_identity_fix.md)。2026-09-28 又完成论文式引用展示，见下节。历史 72 轮及失败计数不改写为成功。M6 的剩余执行步骤以 [优化方案 §12.1](product_experience_optimization_plan.md#121-m6-执行清单2026-09-28-更新) 为准。

## 当前结论

M1–M5 功能已实现，主要浏览器交互回归通过；**M6 质量与性能联合验收仍未全部通过**。
当前配置为 DeepSeek v4 Pro、judge 引用模式和 Tavily basic 搜索。用户追加的知识列表
固定每页 10 份、鼠标点击不显示焦点外圈、键盘焦点保留均已实现。

本轮修正了旧测试中的 100 行列表、旧计数文案及完成后仍保留处理面板的断言。
仍验证 100 份资料全部可翻页到达、末页禁用下一页、筛选回第一页、跨页上传状态保留。
处理详情的低高度检查改为正在进行的确定性 SSE 流，而不是已完成的消息。

## 2026-09-28 引用展示更新

- 正文采用论文式上标 `[1]`；按每条回答的 citations 顺序编号，重复引用沿用编号，后台 C 标记和 evidence_id 对应关系不变。
- 回答底部参考来源、来源面板及顶部回答摘要统一使用展示编号；已有页码才显示，点击仍定位原文。首页示例同步更新。
- 鼠标点击无按钮焦点外圈；键盘保留焦点提示，来源定位采用浅色背景反馈。
- 本次 `npm --prefix frontend test`：92 passed；`npm --prefix frontend run build`：类型检查及构建通过。
- `python tests/e2e_paper_citations.py`：Chrome 1440×900、390×844，非连续 ID、重复引用、来源定位、键盘/鼠标焦点与无横向溢出通过；无 console/pageerror。另通过已有跨轮引用/焦点场景及首页示例点击检查。
- 已查看 `work/paper-citations/answer-1440.png`、`answer-390.png`、`source-390.png`。这些是 API fixtures 下的 UI 证据，未重新运行完整真实模型批次、所有浏览器矩阵或后端全量测试。

本次文档整理仅更新状态与执行说明，不新增运行验收结果。

## 2026-09-28 GitHub 同步前复核

本节记录本次源码同步前重新执行的结果，不覆盖旧批次数据，也不代表 M6 全部通过。

- `python -m pytest tests -q`：253 passed、2 skipped；既有 `python_multipart` 弃用提示。
- `npm --prefix frontend test`：15 个测试文件、92 passed。
- `npm --prefix frontend run build`：类型检查及生产构建通过；既有第三方 PURE 注释提示。
- `e2e_mvp_browser.py`、`e2e_product_experience.py`、`e2e_workspace_design.py`、`e2e_paper_citations.py` 重新通过；使用本机 Chrome 和确定性 API fixtures。
- `e2e_experience_edge_cases.py` 重新通过：Chromium 真实 200% 缩放、触控模拟/嵌套抽屉及 24 组错误/断流场景。
- 独立只读代码审查未发现发布阻断问题；不替代运行测试或真实模型质量审核。
- 本轮未重跑付费真实模型批次，没有新增长期性能、账单成本或真机结论。README、前端 README 及提交边界已同步当前模型/搜索配置、引用、分页和验收范围。

## 2026-09-27 自动化与浏览器证据（历史批次）

| 验证 | 结果 |
|---|---|
| `python -m pytest tests -q` | 247 passed、2 skipped；既有依赖弃用警告 |
| `npm --prefix frontend test` | 91 passed |
| `npm --prefix frontend run build` | 类型检查及生产构建通过；既有依赖注释警告 |
| `python tests/e2e_mvp_browser.py` | 通过 |
| `python tests/e2e_product_experience.py` | Chrome 通过；14 个首页布局、任务状态、IME、引用、100 文档分页/筛选 |
| `python tests/e2e_workspace_design.py` | Chrome 通过；6 个视口、3 个低高度布局、复制/滚动/引用/抽屉焦点 |
| `python tests/e2e_experience_edge_cases.py` | Chromium 通过；真实 200% 缩放、触控模拟、24 组错误/断流场景 |
| Edge 复测上述 product/workspace 六组检查函数 | 全部通过，见 `work/final-acceptance/edge.json` |
| 真实 Tavily + DeepSeek 浏览器问答 | 回答、网页来源面板通过，页面错误为 0；截图已查看 |

缩放脚本运行时设置 `CHROMIUM_EXECUTABLE` 指向本机已安装的
`C:/Users/Website/AppData/Local/ms-playwright/chromium-1246/chrome-win64/chrome.exe`；
默认 Playwright 指向的 chromium-1234 不存在，不将启动失败算成产品失败。
Chrome 与 Edge 都是 Chromium 系，不代表 Firefox/WebKit 已验证，也不代表真实手机软键盘已验证。
浏览器边界用例使用 API fixtures；真实联网浏览器样本将请求转发到隔离的 8113 后端，未伪造模型或搜索结果。

## DeepSeek/Tavily 修复前的 72 轮真实样本（历史批次）

冻结案例 `tests/fixtures/chat_latency_cases.json`，20 案例 × 3 次，含追问共 72 轮。
复用既有合成企业制度，在独立 SQLite、Chroma、上传和 trace 目录执行；未写入日常使用数据。
原始记录：`work/final-acceptance/benchmark/raw.json`、`summary.csv`；汇总 `summary.json`。
包含失败样本，不剔除安全回退。全部 72 轮收到 result，无传输超时；其中 61 次正常回答、
9 次预设资料不足拒答、1 次 Judge 阶段回退、1 次联网答案校验失败。

| 类型 | 轮数 | 正常结果 / 回退 | HTTP result P50 / P95（秒） |
|---|---:|---|---|
| 知识 | 24 | 24 正常 | 7.327 / 9.463 |
| 追问（含各链首问） | 24 | 23 正常、1 generation_error | 8.105 / 9.931 |
| 通用 | 9 | 9 正常 | 6.210 / 10.881 |
| 预期拒答 | 9 | 9 retrieval_miss | 12.368 / 13.185 |
| 联网 | 6 | 5 正常、1 search_answer_error | 5.998 / 7.586 |
| 总体 | 72 | 70 符合结果类别预期、2 非预期回退 | 7.645 / 12.383 |

“结果类别符合预期”不是独立答案准确率。HTTP result 时点不是浏览器绘制时点。
这批样本使用不同于原阿里基线的模型和搜索服务，期间还运行了本机验收，不能据此计算纯代码提速、
宣称达成 15% 目标或给出长期 SLA。下述摘录修复在批次过程中完成，但隔离后端未热加载，
所以 **72 轮代表摘录修复前版本**；修复后证据是全量单测、捕获样例回归与定向真实 Judge 检查。

## 发现的问题与处理

1. **整段末尾共用引用时摘录不完整：已修复。** 捕获到审批答案末尾只有一个 C1，摘录器仅根据
   最后一句挑选“部门负责人”例外，漏掉前面的普通审批条件。现在单来源段落整体参与摘录选择；
   不跨引用编号借用断言，保留软换行，将列表项分开。来源与语义校验职责不变。
   新回归先失败后通过；独立审查发现的软换行边界也已补测。修复后捕获样例真实 Judge 3/3 通过。
2. **追问回退：已定位并完成稳定来源标识修复。** `followup-2` 第二问“需要保留哪些凭证？”第一轮回退，
   后两轮正常。后续从 SQLite 检查点恢复两次原始 Judge 理由：历史差旅资料为 C1，本轮重新检索后
   为 C3，生成器仍引用 C1（合同与采购），Judge 正确拦截。恢复精确历史重放 3/3 失败；只移除
   模型输入中的历史引用编号后 3/3 通过。修复后保留原历史 3/3 正确引用，对照组 3/3 通过，完整隔离问答 7/7 正常；未据此重写旧 72 轮结果。见 [追问回退定位](knowledge_followup_fallback_diagnosis.md) 与 [修复记录](stable_evidence_identity_fix.md)。
3. **联网偶发答案校验失败：保留失败记录。** `web-1`“查找 Python 最新稳定版本”有一次
   search_answer_error。仍保持失败时不交付未经来源校验的答案，不通过放宽引用规则提高成功率。
   额外 3 次定向调用均通过来源编号校验，但其中一次在官方资料列出 3.14 为 bugfix 时，仍采纳
   第三方旧摘要称 3.13.x 为最新稳定版本。这证明来源编号有效不代表事实/时效正确；
   联网资料冲突与权威来源选择仍未通过质量验收。捕获记录为 `web-diagnosis.json`，不能将 3/3
   接口成功写作 3/3 答案正确。

合成知识/追问/拒答共 57 轮正文与引文在隔离目录 `synthetic-answer-audit.json` 留档并按去重内容核对。
核对发现了上述摘录问题；一些简短金额回答未主动展开部门负责人例外，不能由 Judge 通过推导
所有条件完整。此样本不含多版本语料，不构成多版本继承或全网最新事实的全面独立审计。

## 用量与剩余门槛

72 轮请求预算累计：294 次模型调用、6 次搜索工具调用、输入 136939 token、输出 11415 token。
后台标题独立计量：61 次调用、60 完成、1 失败，已返回输入 1633 / 输出 258 token
（含额外真实浏览器会话，不能混入 72 轮主表）。失败调用可能另有未返回用量。
没有已核实的账户账单、缓存命中分类和单价；Tavily credits 也不等于模型 token。
因此不填虚假的金额成本，不将 `estimated_cost=0` 解释为免费。

待执行：最终版本同配置性能/质量对照及分时段样本；账单级成本；真机触屏/软键盘、Firefox/WebKit 和更完整组合矩阵；多版本知识库独立审核。具体命令、证据和通过条件见优化方案 §12.1 A–G。

按用户范围暂缓：联网权威性、时效冲突与完整事实审计；保留 Tavily 可用性/失败反馈检查及已知质量问题，不将暂缓项标记为通过。15% 为性能争取目标，未达到需解释，不等于功能未实现；长期 SLA 仍不作承诺。
这些是明确的验收边界，不以文档勾选替代证据。2026-09-27 验收时未推送；2026-09-28 用户已授权同步源码到 GitHub，推送结果以 Git 远端记录为准，本轮不包含部署或上线。
