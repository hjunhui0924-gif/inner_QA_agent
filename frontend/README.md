# Vue 企业知识工作台

## 技术方案

默认前端采用：

- Vue 3；
- Vite；
- TypeScript 严格模式；
- Vue Router；
- 原生 CSS 设计 Token 与响应式布局。

当前没有引入 Pinia、Tailwind 或大型 UI 组件库。共享状态集中在 Composition API
工作区组合函数中；现有页面规模不需要额外状态库。

## 页面结构

```text
src/
  components/
    AppSidebar.vue
    EvidencePanel.vue
  views/
    ChatView.vue
    KnowledgeView.vue
  styles/
    tokens.css
    base.css
  App.vue
  main.ts
  router.ts
```

- `/chat`：会话侧栏、问答区、快捷问题、输入框和引用证据面板；
- `/knowledge`：标题与来源输入、文件选择、上传入口和知识文件列表；
- 760px 以下使用移动端侧栏；
- 900px 以下引用面板改为按需抽屉。

## 视觉系统

- 背景：暖纸色 `#F4F1EA`；
- 正文：墨色 `#17211D`；
- 主色：森林绿 `#245C47`；
- 辅助状态：琥珀色 `#C78532`；
- 标题使用宋体风格，正文使用中文无衬线字体；
- 主要容器 14px 圆角、控件 8px 圆角；
- 动效控制在 160～240ms，并兼容 `prefers-reduced-motion`。

## 本地开发

```bash
npm install
npm run dev
```

开发地址为 `http://127.0.0.1:5173`。`vite.config.ts` 已预留 `/api` 到
`http://127.0.0.1:8000` 的代理。

检查命令：

```bash
npm run typecheck
npm run test
npm run build
```

## 已实现能力

- Vue TypeScript 工程；
- 桌面问答工作台；
- FastAPI 健康状态与离线提示；
- 会话列表、历史加载、新建、切换和完整删除；
- SSE 流式问答与 Agent 节点状态；
- 结构化引用证据面板和历史引用恢复；
- 知识文件上传、重复提示和知识库列表；
- Markdown 安全渲染、错误提示和删除确认；
- 移动端导航与响应式布局；
- Vitest 的 SSE 分片与错误响应测试；
- 桌面与 390px 移动端真实浏览器联调。

Vue 是项目唯一前端。真实联调覆盖健康检查、文件上传、知识库刷新、RAG 流式回答、
引用展示、会话创建/切换、历史引用恢复和完整删除；测试后端的控制台错误与失败请求
均会作为验收失败处理。
