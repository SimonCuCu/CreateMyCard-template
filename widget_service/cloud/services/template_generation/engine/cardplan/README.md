# CardPlan 压缩代码索引

本目录的 Provider 模板压缩保持原 .cardtpl 为唯一作者源。运行时和落盘快照都只做无损的结构共享。

## 从哪里开始看

- compression.py：结构哈希、精确驻留、近似候选分析和 DAG 落盘序列化。
- registry.py：加载 Provider 模板后调用 intern_template_definitions()；后续检索和编译拿到的是
  压缩后的模板对象。
- ../compression_cli.py：生成分析 JSON 和完整压缩 DAG 快照的命令行入口。
- generated/compressed-provider-component-dag.json：已提交的、可直接查看的全量压缩产物。
- ../../tests/test_template_compression.py：共享等价性和落盘 JSON 的测试。

## 最简命令

~~~bash
PYTHONPATH=widget_service/cloud python3 \
  widget_service/cloud/services/template_generation/compression_cli.py \
  --write-compressed-dag \
  widget_service/cloud/services/template_generation/engine/cardplan/generated/\
compressed-provider-component-dag.json
~~~

## 如何查看 Battery

在 generated/compressed-provider-component-dag.json 的 templates 中查找
BatteryOverviewNormalFull。其 variants[].rootComponentId 是可读的压缩树名称；
rootContentDigest 是机器校验的内容摘要。以摘要去掉 sha256: 前缀后到 nodes 中查找，递归读取
childNodeIds 即可展开压缩后的组件树。多个模板有相同的名称和摘要，代表它们复用了同一组件子树。

这个 JSON 是 compressed-provider-component-dag/1 的检查快照，不是 cardtpl/1 输入文件，也不应手改。
