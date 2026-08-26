# CreateMyCard

111

## 压缩组件库

在 `compress` 分支，压缩后的组件以 Provider 的 `components/*.cardtpl` 直接落盘；模板仍保留原检索 ID，
通过 `UseComponent("组件语义名@版本")` 引用这些组件。查询运行时只加载这套源文件，不读取结构 DAG JSON。

例如电池三张 Full 卡共同引用：

```text
resources/source/providers/battery/components/
  battery-overview-full-status-summary.cardtpl
```

如需生成离线检查用的压缩组件 DAG：

~~~bash
PYTHONPATH=widget_service/cloud python3 \
  widget_service/cloud/services/template_generation/compression_cli.py \
  --write-compressed-dag /tmp/compressed-provider-component-dag.json
~~~

JSON 只用于分析和核验；组件源与加载方式见
widget_service/cloud/services/template_generation/engine/cardplan/README.md。
