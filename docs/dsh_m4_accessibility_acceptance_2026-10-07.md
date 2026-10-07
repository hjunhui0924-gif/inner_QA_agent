# DSH M4 反馈弹层键盘与焦点验收

日期：2026-10-07。补齐 [当前状态](current_status.md) 中的反馈弹层专用键盘/焦点待办；已有提交、幂等和迟到响应逻辑继续回归。真实 Vue 页面和浏览器键盘事件配合合成历史与 API mock，不调用真实模型，不修改日常资料或数据库。

## 缺陷与实现

浏览器先复现：按 Enter 打开反馈后，焦点仍在原反馈按钮，没有进入弹层。原内联表单未注册 overlayStack，因此反馈也未参与项目已有的模态背景隔离、滚动锁定和键盘仲裁。

- `frontend/src/components/FeedbackDialog.vue`：接管反馈表单，用 Teleport 放到 body，并向现有 `overlayStack.ts` 注册模态弹层。打开聚焦选中分类；单选组只保留已选项作为 Tab 停靠点，方向键采用原生行为。每次按 Tab 查询当前有效控件，跳过禁用按钮，正反向循环。Escape 仅由栈顶弹层处理；组合输入事件交给输入法。
- 表单保持可访问名称“反馈回答”，为分类组、补充说明和提示文案提供名称或关联。关闭后返回保存的实际触发按钮，使用 preventScroll；异步焦点任务在再次开关或卸载后失效，避免旧任务抢焦点。
- `frontend/src/views/ChatView.vue`：传递入口元素、草稿和提交状态，保留反馈尝试身份、同内容失败重试 ID 及迟到响应隔离。
- `frontend/src/components/EvidencePanel.vue`：新增 blocked 状态；反馈覆盖已有桌面来源面板时设为 inert/aria-hidden，反馈关闭后恢复来源面板及原弹层栈顺序。
- `frontend/src/styles/overlays.css`：反馈位于来源抽屉之上，表单限制动态视口高度并提供内部滚动，避免内容超出视口。

## 浏览器专项

新增 `tests/e2e_feedback_accessibility.py`，支持浏览器、单项场景与 base URL，失败保留截图和诊断，并返回非零退出码。命令：

```powershell
python tests/e2e_feedback_accessibility.py --browsers chrome msedge firefox webkit
python tests/e2e_feedback_retry.py --browsers chrome msedge firefox webkit
```

| 浏览器 | 本轮版本 | 键盘与焦点 | 反馈重试 |
|---|---|---|---|
| Chrome（本机 channel） | 140.0.7339.186 | 8/8 | 5/5 |
| Edge（本机 channel） | 154.0.4258.53 | 8/8 | 5/5 |
| Firefox（Playwright） | 153.0 | 8/8 | 5/5 |
| WebKit（Playwright） | 26.5 | 8/8 | 5/5 |
| 合计 | — | **32/32** | **20/20** |

每个浏览器覆盖 8 项：

1. 1440×900 键盘打开、初始选中项焦点、分类方向键、Tab/Shift+Tab 完整循环、组合期间 Escape、背景隔离及 Escape 关闭。
2. 390×900 的相同键盘场景。
3. 320×640 的相同键盘场景，额外检查表单边界及无横向溢出。
4. 提交中禁用按钮不进入循环；Escape 关闭并恢复入口焦点，迟到成功不再次打开表单。
5. 502 失败保留说明，提交按钮恢复后仍参与循环；修改说明再次提交成功，关闭并返回原入口。
6. 点击遮罩关闭并恢复入口焦点。
7. 桌面引用面板保持打开，反馈期间不可聚焦；第一次 Escape 仅关闭反馈，再次 Escape 才关闭引用面板并回到引用入口。
8. 用户阅读旧回答时取消反馈，返回同一反馈按钮，四浏览器对话 scrollTop 均由 3734 保持为 3734，仍远离最新回答。

既有重试专项另覆盖同内容失败重试、修改内容、409、关闭重开后迟到成功和迟到失败。结果为独立完整批次，不拼接旧结果。键盘专项的页面错误、意外控制台错误和失败请求均为 0；提交失败场景中故意返回的 502 控制台信息按对应接口单列，未全局忽略错误。

已查看 Chrome 320px 和 WebKit 390px 的打开截图：分类、说明、取消与提交控件均可见，焦点标记可见。

## 回归与证据

| 命令 | 本轮结果 |
|---|---|
| `npm --prefix frontend test` | 17 个文件、140 passed |
| `npm --prefix frontend run build` | 类型检查与生产构建通过；保留既有第三方 PURE 注释警告 |
| `python tests/e2e_mvp_browser.py` | 通过 |
| `python tests/e2e_product_experience.py` | 通过，含首页 14 种布局、请求隔离、IME、引用身份/焦点、草稿/滚动、后台流式导航和文档筛选 |
| `git diff --check`、新增脚本语法与文档链接检查 | 通过 |

本地证据：

- 原缺陷复现：`work/feedback-accessibility/20261007-013133-751688/report.json`，0/1，初始焦点断言失败。
- Chrome 首次实现检查：`work/feedback-accessibility/20261007-013311-388153/report.json`，7/8；剩余失败是测试错误使用只存在于正文引用中的按钮名称，合成历史仅提供底部来源列表，已按实际来源标题修正定位器。
- 最终四浏览器完整键盘批次：`work/feedback-accessibility/20261007-013437-065348/report.json`，32/32。
- 当前代码反馈重试批次：`work/feedback-retry/20261007-013437-076380/report.json`，20/20。
- 截图及失败诊断分别位于上述目录的各浏览器子目录；`work/` 是本地忽略的验收产物。

本轮未改后端反馈存储；历史持久化和权限验收见 [PDF 与反馈重试记录](dsh_pdf_browser_acceptance_2026-10-07.md)。这次模拟视口与组合事件不证明真机输入法、触控、软键盘遮挡、真实模型回答质量或长期性能。剩余验收见 [当前状态](current_status.md)。
