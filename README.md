# CreateMyCard

111

## 压缩组件库

在 compress 分支，Provider 模板会在 Registry 加载后进行无损结构共享。生成可直接查看的压缩
组件 DAG：

~~~bash
PYTHONPATH=widget_service/cloud python3 \
  widget_service/cloud/services/template_generation/compression_cli.py \
  --write-compressed-dag \
  widget_service/cloud/services/template_generation/engine/cardplan/generated/\
compressed-provider-component-dag.json
~~~

输出文件保留所有模板入口、数据契约和共享节点；使用方法见
widget_service/cloud/services/template_generation/engine/cardplan/README.md。
