# DSH PDF 跨浏览器验收与反馈重试补齐

日期：2026-10-07。完成 P1 原页预览的四浏览器验收；同步方案时发现 M4 客户端反馈失败重试未复用 ID，已在 PDF 模块验证之后单独修复并测试。本轮不把这两项结果扩展为真机或业务级答案质量结论。

## PDF 的实际缺陷与修复

新增中文制度 PDF 后，Chrome、Edge、Firefox、WebKit 都不能显示和定位其中的中文正文。pdf.js 日志提示缺少 cMapUrl；此前英文合成 PDF 未覆盖这类依赖外部字符映射的字体，入口和色块正常也不能证明中文字形正常。

- `frontend/tooling/pdfAssets.ts`：从当前安装的 pdfjs-dist 读取 cmaps、standard_fonts、wasm 及许可证。开发服务按明确资源表提供 `/pdfjs/`，生产构建发布同样的资源；未知资源返回 404，不回退为 HTML 页面。保留原包及各目录许可证，不新增依赖或远程 CDN。
- `frontend/vite.config.ts`：接入资源插件；沿用已有 Vue、API 代理和测试配置。
- `frontend/src/components/PdfSourcePreview.vue`：打开原页时向 getDocument 提供随部署 base 变化的 cMapUrl、standardFontDataUrl、wasmUrl；保留独立 worker、渲染取消、文件门禁和唯一文本 run 匹配。
- `tests/browser_pdf_fixtures.py`：新增“差旅报销需要部门经理审批。”的中文合成文件，使用 CJK 字体；不包含企业实际资料。
- `tests/e2e_pdf_source_preview.py`：增加浏览器选择、中文场景、网络记录、失败截图与状态、微秒证据目录及非零失败退出码；原有调用和场景过滤仍可使用。

生产资源共 199 个文件、3,519,456 字节（约 3.36 MiB），与依赖原文件逐字节一致，包含许可证。它们作为独立静态资源按需读取，不加入首页 JavaScript；PDF 下载及 worker 仍在用户明确打开原页后启动。

## PDF 验证

| 验证 | 结果 |
|---|---|
| `python tests/e2e_pdf_source_preview.py --browsers chrome msedge firefox webkit` | 最终单批 64/64；每个浏览器 16 项 |
| `python tests/e2e_pdf_source_preview.py --base-url http://127.0.0.1:4173 --browsers chrome msedge firefox webkit --scenario render-390 --scenario chinese --scenario worker-failure --scenario citation-page-2` | 生产构建 16/16；每个浏览器 4 项 |
| 构建资源检查 | 199 个文件与原依赖一致，字体、字符映射、WASM 和许可证均存在 |

生产页面通过临时 `npm --prefix frontend run preview -- --host 127.0.0.1 --port 4173 --strictPort` 启动，验证后关闭该临时服务；原有 5173 服务保留。

| 浏览器 | 本轮版本 | 完整专项 | 生产构建 |
|---|---|---|---|
| Chrome（本机 channel） | 140.0.7339.186 | 16/16 | 4/4 |
| Edge（本机 channel） | 154.0.4258.53 | 16/16 | 4/4 |
| Firefox（Playwright） | 153.0 | 16/16 | 4/4 |
| WebKit（Playwright） | 26.5 | 16/16 | 4/4 |

每个浏览器覆盖：1440/390/320px 有效 PDF、中文、扫描页、重复短句、损坏、加密、字节超限、页数超限、worker 失败、403、404、下载中关闭、引用第 2 页和页码越界。有效文件检查真实画布色块、文本高亮覆盖字形、可选择文字、200% 放大、24 轮翻页/缩放、适应宽度、关闭重开、worker 释放及详情入口焦点恢复。中文另确认读取本地字符映射资源。

最终 PDF 的页面错误、意外失败请求均为 0。主动超限取消下载、故意阻断 worker、关闭下载及 SSE done 后停止读取的取消记录保留为预期行为；只按具体场景与对应 URL 放行，不全局忽略网络错误。已查看 Chrome 中文 390px 和 WebKit 320px 截图，中文字形、高亮、工具栏与内部滚动可见。

## M4 客户端缺口与修复

原实现每次点击提交都生成新的 feedback_id。浏览器先复现：模拟服务端保存反馈后返回 502，用户保持原内容重试，两次请求使用不同 ID。这使服务端已有的 `(user_id, feedback_id)` 幂等约束无法将两次请求归为同一条。

`frontend/src/views/ChatView.vue` 现保存反馈尝试快照：

- 相同会话、轮次、回答、分类和说明的失败重试复用同一 ID；分类或说明改变后生成新 ID，避免复用 ID 携带不同内容触发冲突。
- 409 显示明确提示；不自动重发。失败保留分类和说明。
- 关闭重开或切换会话清理旧尝试；旧请求成功或失败不关闭新表单，也不把旧错误写入新反馈。
- 成功仅关闭仍对应当前尝试且未修改内容的表单；提交中编辑的新草稿不被旧成功响应清除。

新增 `tests/e2e_feedback_retry.py`。四浏览器各执行 5 项：同内容失败重试、编辑后重试、409 后修改提交、关闭重开后迟到成功、关闭重开后迟到失败。最终 **20/20**，页面错误为 0。同内容的两次请求完整 payload 一致，模拟保存结果仅一条；改变内容或重新打开后 ID 不复用。此处的保存计数属于 API-mock 状态，不冒充真实生产数据库验收。

反馈修复阶段曾因保存前的普通对象与 Vue 响应式代理引用不一致，使提示和关闭未生效；已改为捕获 ref 保存后的对象。失败批次保留，最终完整批次独立重跑通过。反馈专用键盘、Escape 和焦点循环未纳入本次请求专项，仍单列待验收。

## 最终回归与证据

| 命令 | 结果 |
|---|---|
| `npm --prefix frontend test` | 最新代码 17 个文件、140 passed |
| `npm --prefix frontend run build` | 最新代码通过，包含 Vue/TypeScript 检查；保留既有第三方 PURE 注释警告 |
| `python -m pytest tests/test_feedback.py -q` | 1 passed；使用隔离临时 SQLite，验证存储与拥有者隔离；本轮未修改后端 |
| `python tests/e2e_feedback_retry.py --browsers chrome msedge firefox webkit` | 20/20 |
| `python tests/e2e_mvp_browser.py` | 通过 |
| `python tests/e2e_product_experience.py` | 最新代码通过，包含 14 种首页布局、请求隔离、IME、引用身份/焦点、草稿/滚动和后台流式导航等既有流程 |
| `git diff --check` | 通过 |

本地证据：

- PDF 首次失败：`work/pdf-source-preview/20261007-005136-259093/report.json`，56/64；4 项中文缺陷与 4 项预期取消分类问题。对应失败截图和 JSON 保留。
- 中文修复复测：`work/pdf-source-preview/20261007-005550-346482/report.json`，4/4。
- PDF 最终完整批次：`work/pdf-source-preview/20261007-005807-870815/report.json`，64/64。
- PDF 生产构建：`work/pdf-source-preview/20261007-005853-992281/report.json`，16/16。
- 构建日志：`work/pdf-source-preview/build-20261007.log`；反馈修复后的最新构建为 `work/pdf-source-preview/build-feedback-20261007.log`。
- 反馈原缺陷复现：`work/feedback-retry/20261007-010856-173963/report.json`，0/1；修复阶段失败记录 `20261007-010921-671025`，0/12；修正引用捕获后 `20261007-011129-269836`，12/12；最终增加冲突/迟到错误场景后 `work/feedback-retry/20261007-011320-242653/report.json`，20/20。
- 截图与合成 PDF 均在忽略的 work 目录；既有产品体验目录会复用，本次结果以命令输出为准。

浏览器为真实 Vue/pdf.js/worker，文件为合成有效 PDF，API 为 mock。仅保证已执行文件与场景，不证明全部企业文件排版、真机原生触控和软键盘、真实模型质量、实际反馈运营或长期性能。历史 P1/M6 报告保持各自日期和范围；剩余工作见 [当前状态](current_status.md)。未提交、推送或部署，保留已有在途修改与本地资料。
