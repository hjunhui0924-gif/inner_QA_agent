# DSH P1 来源预览补齐与验收

日期：2026-10-06。范围为内部知识助手的引用核验、长文抽取文本窗口与 PDF 原页。此前 PDF 只验证入口和工具栏，本次补齐实际 canvas/文本层显示与失败恢复。

## 实际变更

- `PdfSourcePreview.vue`：首次打开即加载，canvas 和文本容器保持挂载；懒加载 pdf.js 兼容构建，独立 worker，串行取消旧渲染、代次检查、关闭取消下载和销毁资源。引用页码越界回第一页，默认适应宽度，50%–200% 缩放；单次 canvas 限 16,000,000 像素、单边限 8,192。
- `api.ts`、`routes.py`、`config.py`、`main.py`：授权下载的预览模式、默认 20 MiB / 500 页和 CORS 限制头；前端在完整拼接前检查长度并累计流式字节，超限取消。原有授权下载保留原行为，原文件可用性按上传根目录内的真实文件判断。
- `pdfText.ts`：按 pdf.js 文本 run 查找，仅唯一命中使用真实文本节点高亮；支持空白归一化与 emoji，不使用抽取文本偏移猜测 PDF 坐标，不拼接 HTML。
- `source_preview.py`：24,000 code points 限制改为单窗口，长文后半部分可继续浏览和定位；窗口最多 12 块、单块最多 4,000，前后 cursor 按同一限制衔接。
- `EvidencePanel.vue`、`DocumentDetailDrawer.vue`：引用页码和摘录传入原页，抽取文本高亮使用 code-point 偏移，显示版本变化/未知与文件缺失，支持回到引用；请求绑定当前 session/turn，关闭和切换取消请求，权限错误清除缓存正文，迟到响应不回写。
- 浏览器测试依赖新增 PyMuPDF，仅列于 `requirements-e2e.txt`，用于生成合成文件，不加入生产运行依赖。

## 已执行验证

| 检查 | 结果及范围 |
|---|---|
| `npm --prefix frontend test` | 108 passed；新覆盖唯一/重复/安全文本高亮、下载字节限制、emoji、旧响应、会话隔离与撤权清理 |
| `npm --prefix frontend run build` | 通过，包含 Vue/TypeScript 检查；PDF 和 worker 独立产物，保留既有第三方 PURE 注释警告 |
| `python -m pytest tests/test_source_preview.py tests/test_source_preview_api.py tests/test_pdf_preview.py tests/test_access_control.py tests/test_knowledge_ingestion.py tests/test_citations.py -q` | 67 passed；来源 ACL、存储引用绑定、cursor/版本变化、长文窗口、旧记录与下载预览门禁 |
| `python tests/e2e_pdf_source_preview.py` | Chrome 15/15；实际 pdf.js/worker 处理有效合成 PDF，页面像素和文本层均验证 |
| `python tests/e2e_pdf_source_preview.py --base-url http://127.0.0.1:4173 --scenario render-390 --scenario worker-failure` | 生产构建 2/2；真实构建资产的渲染、翻页缩放、worker 失败与重试 |
| `python tests/e2e_mvp_browser.py` | 通过；问答→引用→来源、焦点/关闭、知识管理和下载 |
| `python tests/e2e_product_experience.py` | 通过；14 种首页布局、请求隔离、IME、证据身份/焦点、草稿/滚动、流式导航、慢会话和上传等既有场景 |
| `git diff --check` | 通过；无新增空白错误 |

15 项 PDF 场景为：1440/390/320px 有效文件；扫描页；重复短句；损坏文件；加密文件；字节超限；页数超限；worker 失败；403；404；下载中关闭；引用第 2 页；引用页码 999。三种视口均断言页面颜色像素、翻页、200% 缩放、可选择文本、唯一 mark 覆盖字形像素、24 轮连续翻页/缩放、收起重新打开、worker 回收和焦点恢复。异常文件修复或网络恢复后重试有效文件，页面错误总计 0。

证据目录：

- 开发页面完整专项：`work/pdf-source-preview/20261006-232458/report.json` 与 `pdf-1440.png`、`pdf-390.png`、`pdf-320.png` 等截图。
- 生产构建冒烟：`work/pdf-source-preview/20261006-232709/report.json` 与截图。
- 已人工查看桌面和 390px 截图，正文图像、文本高亮和工具栏可见；截图与临时 PDF 均在忽略目录，不写入日常上传资料。

## 验证边界与实现差异

浏览器使用真实 Vue/pdf.js/worker 与有效 PDF 字节，API 响应使用 mock；后端引用、权限和窗口另经 API 函数测试验证。未调用真实模型，不证明回答正确率或企业实际资料的全部排版兼容性；本轮 PDF 未执行 Firefox/WebKit/真机专项。

只保证当前可访问原文件。抽取文本 checksum 不证明原文件字节或历史文件版本不变。扫描件可显示图像页，无 OCR；加密文件提示本地下载后用密码打开。引用断行、跨文本 run、复杂排版或重复句无法可靠映射时明确降级，不宣称精确定位。

本轮沿用详情抽屉的原文/文档信息纵向分区，未新增独立页签或 5 份全文缓存；关闭和会话切换即清理预览。后续按需要改进阅读布局，不把未实现的独立页签、历史文件快照、OCR、真机或跨浏览器 PDF 体验写为已验收。

下一阶段为 DSH M6 SSE 与滚动专项，先复核已有测试覆盖再补缺口。既有 2026-10-02 综合质量报告保留其日期和验证边界。
