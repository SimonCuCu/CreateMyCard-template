# Compact/Terse 模板路由与双产物设计

## 设计目标

模板是 Compact 和 TerseDSL-Nested-2 create 场景的内部生成方式，不新增外部协议。原始入口只负责构造
既有 `GenerationRoutePolicy`，随后调用对应模板路由门面。Compact 保留 dev 原有回退契约；Terse 使用严格
模板契约。

## 路由状态机

```text
generateWidgetCardCompactDsl
  └─ route_compact_generation
       ├─ edit
       │    └─ 原始 Compact 流程
       └─ create
            ├─ 准备 dev 能力裁决、CardSpec、TaskSpec
            ├─ 第一层 LLM：只从候选字段中提取 query 必显字段并选择 Theme
            ├─ CardTpl Variant 字段 Token 集合检索
            │    ├─ 必显字段不属于候选或模板未消费任一必显字段 → 原始 Compact 流程
            │    └─ Theme、字段、类型、尺寸和准入全部匹配 → 锁定一个模板 Variant
            ├─ 检索外适配层：由模板映射内部业务组件范围
            ├─ 第二层 LLM：只生成受限布局和模板调用
            ├─ 服务端解析、参数校验、模板展开
            ├─ 内部 A2UI 适配当前 dev Form profile
            ├─ A2UI → A2UI-Compact
            ├─ dev Compact Processor → 最终 A2UI
            ├─ dev ArtifactValidator
            └─ ArtifactStore 保存 genui + designcompactdsl
```

```text
generateWidgetCardTerseDslNested2
  └─ route_terse_nested2_generation
       ├─ edit → failed
       └─ create
            ├─ 第一层 LLM 只提取 query 必显字段和 Theme
            ├─ CardTpl Variant 字段 Token 集合检索
            │    └─ 未匹配、字段未完整覆盖或模型不可用 → failed
            ├─ 第二层 LLM、参数校验和模板展开
            │    └─ 任一失败 → failed
            ├─ 展开后的 TerseDSL-Nested-2 → 模块内隔离转换器 → 最终 A2UI
            ├─ dev ArtifactValidator
            └─ ArtifactStore 保存 genui + 展开后的 TerseDSL-Nested-2
```

## 为什么先归档 Compact 再确定最终 A2UI

模板编译器先产生内部 A2UI，但 artifact 中的 A2UI-Compact 会在后续 edit 中由原始 dev Processor 读取。
如果首次展示直接保存模板编译器输出，后续 Processor 的规范化可能造成视觉或逻辑漂移。

因此本模块执行以下闭环：

1. 模板内部 A2UI 仅作为中间结果。
2. 适配当前 dev 的 `catalogId`、root 尺寸、圆角和裁剪约束。
3. 确定性生成 A2UI-Compact。
4. 使用原始 dev Compact Processor 将该 Token 转回标准 A2UI。
5. 将回转结果作为首次展示的最终 A2UI，并将同一个 Token 写入 `designcompactdsl`。

这样首次展示和二次更新共享同一条 Compact 转换链。

## 失败与回退边界

| 阶段 | 行为 | 原因 |
|---|---|---|
| edit 请求 | 原始流程 | 二次更新不重新选择模板 |
| 无真实模型运行时 | 原始流程 | 保持 dev mock 和既有测试行为 |
| 第一层输出非法或异常 | 原始流程 | 用户强诉求无法形成可信检索输入 |
| Variant 检索未命中或异常 | 原始流程 | 内部不重试、不改写诉求、不切换检索方式 |
| 第二层或模板编译失败 | 返回 failed | 模板路由已经锁定，禁止静默换实现 |
| Compact 归档或 Validator 失败 | 返回 failed | 禁止保存无法稳定编辑的半成品 |

`candidateOutputFields` 是可用候选集合，不是强制展示集合。第一层只输出 `themeId` 与 query 实际要求的
`requiredOutputFieldsByCapability`，不判断模板是否可用，也不选择模板或高级组件。无法完整映射强诉求或涉及
多个数据能力时输出空字段集合，服务端据此判定未匹配。

检索索引随 Registry 加载到内存。每个 CardTpl Variant 的 required binding 和 required 非素材参数来源被
规范化为 `(capabilityId, JSON Pointer, type)` 字段 Token。匹配要求“用户强诉求字段集合 ⊆ Variant required
字段集合 ⊆ TaskSpec 实际可用字段集合”，并独立校验 Theme、尺寸、hero role 与 Provider admission。optional
字段不进入 required 集合，因此不枚举 `2^n` 组合；集合使用 `frozenset` 直接判断，不计算整 Schema Hash。
多个结果按额外 required 字段最少、模板 ID、Variant 名稳定排序后只返回第一个。检索结果只包含 Theme、模板
和 Variant，高级组件由检索外适配层映射。

`before_model_call` 由门面包装为单次通知。第一层已经触发通知时，即使回退原始模型，也不会重复下发开始事件。

Terse 路线没有回退分支：create 模板不匹配时返回 `failed`，edit 也直接返回 `failed`。旧 Python 模板
流水线仅通过 `legacy_python.route_legacy_python_terse_generation(...)` 作为问题定位入口保留；生产默认入口
不引用该函数，`widget_generation_service.py` 中的切换点注释用于需要时进行临时对照。

## 对原始 dev 的修改边界

`widget_generation_service.py` 只增加公共入口 import，并将 Compact、Terse 两个入口分别收敛为一次门面调用。
A2UI Form、能力注册、API、配置、日志和批量接口均不需要为模板功能修改。

模板渲染需要的附加候选字段由 `binding_dependencies.py` 在模板路由内补齐，不修改通用能力模型、能力注册表
或 `DeviceCapabilityResolver`。
