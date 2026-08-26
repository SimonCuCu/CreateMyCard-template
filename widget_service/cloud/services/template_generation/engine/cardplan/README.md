# CardPlan 压缩组件库索引

`compress` 分支的正式作者源是 Provider 下的 `.cardtpl` 文件。模板仍按原模板 ID 检索；模板体可以用
`UseComponent("组件语义名@版本")` 引用同一 Provider 的 `components/` 文件。编译器会在校验与渲染前展开
组件，因此端侧拿到的 Form 原子组件树与压缩前一致。

## 从哪里开始看

- `provider_bundle.py`：加载 `components/*.cardtpl`，展开 `UseComponent(...)`，再执行原有的绑定、参数和
  节点校验。
- `resources/source/providers/<provider>/components/`：压缩后真正落盘、可直接查看的组件源。
- `resources/source/providers/<provider>/templates/`：保留原检索 ID、数据契约和薄入口。
- `registry.py`：只加载并编译源文件；不再进行运行时结构驻留，也不读取 DAG JSON。
- `compression.py` 与 `../compression_cli.py`：离线分析、等价性检查和可选 JSON 报告，不参与 query 运行时。

## 如何查看 Battery

先看 `resources/source/providers/battery/templates/battery-overview.cardtpl` 中的
`BatteryOverviewNormalFull@1`、`BatteryOverviewChargingFull@1` 与 `BatteryOverviewLowFull@1`：三者都只有一行
`UseComponent("BatteryOverview.FullStatusSummary@1")`。

实际组件内容在
`resources/source/providers/battery/components/battery-overview-full-status-summary.cardtpl`。这一个文件是三张
卡共同使用的完整 TPL 组件体。

## 可选检查报告

如需重新生成整库的结构分析 JSON，可执行：

~~~bash
PYTHONPATH=widget_service/cloud python3 \
  widget_service/cloud/services/template_generation/compression_cli.py \
  --write-compressed-dag /tmp/compressed-provider-component-dag.json
~~~

它是离线检查产物，不是运行时输入，也不应手改。
