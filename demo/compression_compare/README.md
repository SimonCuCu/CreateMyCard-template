# 原组件库与压缩组件库本地对比

这个 Demo 将同一条 query 分别发送给两个独立的 CreateMyCard 服务：

- 原组件库：`/Users/simonhcb/Desktop/huawei/CreateMyCard 9.34.06 AM`
- 压缩组件库：当前仓库（`compress` 分支）

每侧都走云侧已有的 WebSocket 链路：能力概述、数据 Schema、`generateWidgetCardCompactDsl`、artifact 下载。
Demo 只补齐仓库外的宿主 Agent 候选规划；目前明确覆盖电量 query 和天气 query。云侧服务仍负责模板检索、组件选择、Compact DSL 转换、校验与 artifact 持久化。

## 启动

不要把密钥写入文件。先在终端临时设置 DeepSeek 密钥，再执行：

```zsh
export WIDGET_SERVICE_DEEPSEEK_API_KEY='你的 DeepSeek API Key'
./demo/compression_compare/run_demo.sh
```

脚本会启动原库 `8855`、压缩库 `8856` 和比较页 `8870`；浏览器打开 <http://127.0.0.1:8870>。按 `Ctrl-C` 会同时停止这三个由脚本启动的本地服务。

## 渲染边界

`renderer.js` 是从 miniWidget 的 `web/renderer.js` 原样复制的渲染器，两栏共用同一份代码。它的正式 v1 映射只接受 `Text`、`Row`、`Column`，而 CreateMyCard 真实 artifact 还可能有 `Stack`、`Progress`、`Image`。

因此 `a2ui_adapter.js` 在调用渲染器前做了可见、有限的兼容映射：`Stack` 转为 `Column`，`Progress` 转为数值文本，`Image` 转为占位符文本；并仅传递 miniWidget v1 支持的样式字段。真实 genui 始终可在页面每张卡片下方展开查看。这个预览用于结构与文本对比，不是鸿蒙原生的像素级截图。

## 文件索引

- `server.py`：完整云侧调用、artifact 获取和本地 HTTP 接口。
- `index.html`：输入页和左右卡片展示。
- `a2ui_adapter.js`：CreateMyCard artifact 到 miniWidget v1 消息的边界适配。
- `renderer.js`：miniWidget 原始 HTML/A2UI 渲染器副本，不在此改写。
- `run_demo.sh`：同时启动两套服务和比较页。
