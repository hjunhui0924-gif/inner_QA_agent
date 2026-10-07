# DSH M6 SSE 与滚动专项验收

日期：2026-10-07。内部知识助手的流式交付、取消重试和阅读位置专项已完成。此处的 DSH M6 与 [2026-10-02 业务/性能 M6](m6_acceptance_2026-10-02.md)属于不同计划；本次结果不关闭后者的独立业务质量、性能对照和真机待办。

## 实际变更

| 文件 | 最终行为 |
|---|---|
| `frontend/src/utils/streamEvent.ts` | 事件进入 UI 前校验类型、引用、过程摘要和上下文统计；非法字段报协议错误，合法 pending 摘要步骤可通过；枚举字段必须为字符串 |
| `frontend/src/services/api.ts` | 流式 UTF-8 解码、LF/CRLF、多行 data、注释心跳和完整空行帧边界；区分 JSON 解析和消费者回调错误；取消 reader 并释放锁；done 不等待 HTTP EOF |
| `frontend/src/utils/streamState.ts` | 旧 preview generation 被替代后不能重新覆盖预览；第一个有效 result 保持权威，后续结果和迟到事件不改写正文、引用或 trace |
| `frontend/src/composables/useWorkspace.ts` | 请求捕获各自的 AbortController；取消、已结束和已提交结果的回调不能污染当前步骤及上下文统计；正式结果后的断流保留已交付答案 |
| `frontend/src/views/ChatView.vue`、`frontend/src/styles/chat.css` | 有进度步骤的取消请求仍显示重新生成；ResizeObserver/RAF 跟随布局；阅读时保存可见段落位置；识别向上滚动意图，回到底部或返回最新恢复跟随；End 复用立即返回最新动作；关闭引用恢复焦点时避免额外滚动；关闭/切换清理监听与锚点 |
| 对应单测、`tests/e2e_stream_scroll.py` | 传输故障、事件隔离、取消重试、布局与输入意图验收；记录浏览器版本、错误、失败阶段及截图；输出目录使用微秒后缀防止并发碰撞 |

正式答案只由有效 result 提交。done 无 result、未终止的 SSE 尾帧、无效字段和结果前断流都按未完成处理；result 后断流不撤销答案。业务回退显示业务提示，传输失败显示重新生成入口。没有自动重发生成 POST；主动重试使用新 turn_id 并保留问题分组及 retry_of_turn_id。

## 已执行验证

以下最终前端检查均在 End 修正之后执行。后端相关回归在本轮前期执行；之后未修改后端。

| 命令 | 结果 |
|---|---|
| `npm --prefix frontend test` | 17 个文件，140 passed；API 文件 37、状态 reducer 12、工作区 25 项均通过 |
| `npm --prefix frontend run build` | 通过，包含 Vue/TypeScript 检查；保留既有第三方 PURE 注释警告 |
| `python -m pytest tests/test_api_streaming.py tests/test_ux_delivery.py tests/test_request_budget.py tests/test_session_deletion.py -q` | 26 passed；流式交付、请求预算及会话删除等服务端契约 |
| `python tests/e2e_stream_scroll.py --browsers chrome msedge firefox webkit` | 最终单一批次 56/56；源码在整批执行期间保持不变 |
| `python tests/e2e_conversation_navigation.py` | 6/6；1440/390/320px 的 2/20/100 轮导航、生成中导航、重试分组 |
| `python tests/e2e_product_experience.py` | 通过；14 种首页布局、请求隔离、IME、引用身份/焦点、草稿/滚动恢复、后台流式导航、慢会话与上传等既有场景 |
| `git diff --check` | 通过 |

最终浏览器版本与结果：

| 浏览器 | 版本 | 专项 |
|---|---|---|
| Chrome（本机 channel） | 140.0.7339.186 | 14/14 |
| Edge（本机 channel） | 154.0.4258.53 | 14/14 |
| Firefox（Playwright） | 153.0 | 14/14 |
| WebKit（Playwright） | 26.5 | 14/14 |

每个浏览器执行 7 项传输场景、6 项滚动场景和 1 项输入意图场景：

- 传输：中文/emoji 字节拆分、CRLF、多行 data 与心跳；第一个 result、重复/迟到事件、done 后响应体仍打开；结果前/后断流；无效 result；业务回退；有执行步骤的取消与主动重试；旧预览代次迟到。每次还检查下一条草稿保留、候选 token 不进入正式答案和生成 POST 次数。
- 滚动：1440×900、390×844、320×844，各覆盖 reduce/no-preference；初次生成跟随、输入区增高、35px 向上滚动暂停、输入区缩小、正式回答替换、返回最新及后续高度增长。桌面另覆盖引用面板开合时可见段落锚点保持；阅读位置偏移断言小于 3px。
- 输入意图：实际键盘 ArrowUp/End；合成 touchstart/touchmove 与程序滚动；手动回底部后恢复跟随。键盘向上动画先等待位置稳定，避免把浏览器动画当作页面自动跟随。

最终 56 项的 page error、console error 和 failed request 均为空。已查看最终 Chrome 桌面、Edge 390px 及 Chrome 320px 截图，输入区、正文和按钮可见，窄屏无横向溢出。

## 失败与修复记录

| 批次 | 实际结果与处理 |
|---|---|
| `20261007-000800` | 首批 51/52；WebKit 320px 普通动画跟随超时，不以单项复测代替完整验收 |
| `20261007-001146` | WebKit 滚动 4/6；初次跟随和输入区增高超时。改为按用户跟随意图判断，避免将布局产生的临时 bottom gap 当作向上阅读；随后 `20261007-001450` 的 7/7 通过 |
| `20261007-001742-367647` | 54/56；两处失败现场回到空会话，发生在事件校验文件修改期间，有开发服务重载干扰，未作为稳定源码的验收批次 |
| `20261007-002025-782101` | 稳定源码下 55/56；Edge End 后继续生成时脱离底部。原生键盘动画保留旧底部目标，改为复用立即返回最新；`20261007-002309-502143` 原失败场景 1/1 通过 |
| `20261007-002328-180873` | End 修正后重新运行完整四浏览器，56/56 通过；本记录采用这一单一最终批次，不合并旧结果 |

另有一次并发脚本同秒创建证据目录触发 FileExistsError，属于测试产物命名问题；微秒后缀修正后没有重现。所有失败批次保留原始 report.json 和已生成截图。

## 本地证据与范围

- 最终 M6：`work/dsh-frontend/m6/20261007-002328-180873/report.json`；每个浏览器子目录保存传输、滚动与输入场景截图。
- 最终 M5 回归：`work/m5-navigation/20261007-002328/report.json`。
- 既有产品体验：`work/product-experience/final/`。该脚本目录会复用，具体本轮结果以以上命令输出为准。
- 失败批次位于 `work/dsh-frontend/m6/`。均为忽略的本地产物，不写入日常知识库或会话资料。

浏览器使用真实 Vite/Vue 页面与可控 ReadableStream，REST API 和历史为合成 mock；证明前端协议处理和交互行为，不证明真实网络下的长期稳定性、真实模型回答质量或企业实际数据的全部性能。普通通用非联网回答允许预览，知识库/联网回答仍按既有契约校验后交付。

触摸事件和手机视口为模拟，未验收真机软键盘、中文输入法及原生触控惯性。M6 矩阵未加载 PDF，因此不扩展 P1 的跨浏览器原页验收结论。独立业务质量、同配置性能对照、成本与长期负载证据仍见 [当前状态](current_status.md)。

本轮未提交、推送或部署；保留既有在途修改、本地配置、数据库和上传资料。
