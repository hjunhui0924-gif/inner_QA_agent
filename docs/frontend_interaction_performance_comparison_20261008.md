# 前端交互性能轻量对照（2026-10-08）

本次对照使用 Chrome 140、Windows 11、1440×900、`prefers-reduced-motion: reduce` 和同一套本地合成 API。before 数值来自 2026-10-07 基线记录，after 数值来自当前 `useWorkspace`/Composer 拆分后的代码。两次均不调用模型、不访问外网。

| 消息数 | before 历史加载 | after 历史加载 | before 滚动 | after 滚动 | before 引用面板 | after 引用面板 | before 首个 status | after 首个 status |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 4 | 365.2ms | 841.3ms | 59.88fps | 59.84fps | 43.2ms | 41.1ms | 10.7ms | 14.8ms |
| 40 | 340.9ms | 395.0ms | 60.12fps | 60.25fps | 43.0ms | 45.1ms | 13.8ms | 12.2ms |
| 200 | 431.8ms | 427.5ms | 60.36fps | 60.18fps | 58.0ms | 50.0ms | 21.9ms | 21.1ms |
| 600 | 653.2ms | 638.9ms | 60.51fps | 60.74fps | 68.7ms | 66.5ms | 48.9ms | 39.5ms |
| 1000 | 885.5ms | 838.2ms | 59.78fps | 59.97fps | 88.5ms | 84.4ms | 63.3ms | 69.7ms |

这组数据没有形成稳定、可推广的性能提升结论：滚动帧率和引用面板耗时保持同一量级，历史加载差异受浏览器冷启动和本地运行时波动影响。Demo 阶段保持当前 DOM 方案，不引入虚拟列表；后续若有真实长会话压力，再按 DeepSeek Harness 的变量高度和语义锚点方案单独验证。
