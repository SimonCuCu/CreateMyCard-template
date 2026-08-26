"""Focused tests for deterministic Template compression primitives."""

from __future__ import annotations

from services.template_generation.engine.cardplan.compression import (
    CompressionConfig,
    TemplateCompressionAnalyzer,
    canonicalize_definition,
    compare_trees,
    expand_pattern,
    intern_template_definitions,
    validate_component_dag,
)
from services.template_generation.engine.cardplan.models import (
    TemplateBinding,
    TemplateDefinition,
    TemplateNode,
    TemplateValue,
    TemplateVariant,
)


def _literal(value: str | int) -> TemplateValue:
    return TemplateValue(kind="literal", value=value)


def _object(**values: TemplateValue) -> TemplateValue:
    return TemplateValue(kind="object", properties=values)


def _node(component: str, *children: TemplateNode, **styles: int) -> TemplateNode:
    values = (_object(styles=_object(**{key: _literal(value) for key, value in styles.items()})),)
    return TemplateNode(component=component, values=values, children=children)


def _definition(template_id: str, root: TemplateNode) -> TemplateDefinition:
    variant = TemplateVariant(
        size="full",
        parametersSchema={"type": "object", "properties": {}, "additionalProperties": False},
        root=root,
        expandedNodeBudget=20,
        expandedDepthBudget=5,
    )
    return TemplateDefinition(
        templateId=template_id,
        version=1,
        description="compression fixture",
        domainTags=("fixture",),
        compatibleThemeProfileIds=("fixture-theme",),
        allowedParentComponents=("$root",),
        actionPolicy="none",
        supportedSizes=("full",),
        allowedDesignTokens=(),
        allowedLayoutTokens=(),
        providerId="example.provider",
        businessId="BatteryOverview",
        sourceFormat="cardtpl/1",
        variants=(variant,),
    )


def _ring(diameter: int) -> TemplateNode:
    return _node(
        "Stack",
        _node("Progress", width=diameter, height=diameter),
        _node("Image", width=24, height=24),
        width=diameter,
        height=diameter,
    )


def test_exact_subtree_hash_consing_reports_shared_ring() -> None:
    first = _definition("BatteryNormalFull", _node("Column", _ring(52), width=160))
    second = _definition("BatteryChargingFull", _node("Column", _ring(52), width=160))

    report = TemplateCompressionAnalyzer().analyze((first, second))

    assert report.semantic_similarity_enabled is False
    assert any(
        candidate.component == "Stack" and candidate.occurrence_count == 2
        for candidate in report.exact_candidates
    )


def test_attribute_diff_and_pattern_expand_without_loss() -> None:
    first = _definition("BatteryNormalFull", _ring(52))
    second = _definition("BatteryNormalCompact", _ring(44))
    analyzer = TemplateCompressionAnalyzer(
        CompressionConfig(max_attribute_penalty_ratio=1.0)
    )

    report = analyzer.analyze((first, second))
    candidate = next(
        item
        for item in report.similar_candidates
        if item.left.path == () and item.right.path == ()
    )

    assert candidate.diff.structural_penalty == 0
    assert candidate.diff.changed_attributes == 4
    assert candidate.pattern is not None
    assert expand_pattern(candidate.pattern, candidate.pattern.left_values).digest == (
        candidate.left.node.digest
    )
    assert expand_pattern(candidate.pattern, candidate.pattern.right_values).digest == (
        candidate.right.node.digest
    )


def test_binding_type_change_is_a_contract_violation() -> None:
    root = TemplateNode(
        component="Progress",
        values=(TemplateValue(kind="binding", name="metric"),),
    )
    first = _definition("First", root).model_copy(
        update={"bindings": {"metric": TemplateBinding(path="/value", type="number")}}
    )
    second = _definition("Second", root).model_copy(
        update={"bindings": {"metric": TemplateBinding(path="/value", type="string")}}
    )
    left = canonicalize_definition(first)[0].root
    right = canonicalize_definition(second)[0].root

    diff = compare_trees(left, right)

    assert diff.changed_bindings == 1
    assert diff.contract_violations == 1


def test_component_dag_rejects_cycles() -> None:
    assert validate_component_dag({"atom": (), "ring": ("atom",)}) == (
        "atom",
        "ring",
    )

    try:
        validate_component_dag({"left": ("right",), "right": ("left",)})
    except ValueError as error:
        assert "cycle" in str(error)
    else:
        raise AssertionError("cycle must be rejected")


def test_interning_shares_equal_nodes_without_changing_definitions() -> None:
    first = _definition("BatteryNormalFull", _node("Column", _ring(52), width=160))
    second = _definition("BatteryChargingFull", _node("Column", _ring(52), width=160))

    definitions, metrics = intern_template_definitions((first, second))

    assert definitions[0].model_dump() == first.model_dump()
    assert definitions[1].model_dump() == second.model_dump()
    assert definitions[0].variants[0].root is definitions[1].variants[0].root
    assert metrics.template_count == 2
    assert metrics.shared_node_references > 0
