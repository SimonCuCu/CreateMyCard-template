"""Deterministic, read-only compression analysis for trusted CardPlan Templates."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from .models import TemplateDefinition, TemplateNode, TemplateValue, TemplateVariant

_SAFE_PARAMETER_KINDS = frozenset({"literal", "parameter", "binding"})
_FORBIDDEN_COMPONENT_REPLACEMENTS = frozenset(
    {
        "Button",
        "Checkbox",
        "IconAction",
        "PillAction",
    }
)


@dataclass(frozen=True)
class CanonicalAttribute:
    """One flattened Template value with its closed type information."""

    path: str
    kind: str
    value: str | int | float | bool | None
    value_type: str
    required: bool

    def identity(self) -> tuple[str, str, str | int | float | bool | None, str, bool]:
        return self.path, self.kind, self.value, self.value_type, self.required


@dataclass(frozen=True)
class CanonicalNode:
    """Normalized ordered attributed DOM node used by the analyzer."""

    component: str
    attributes: tuple[CanonicalAttribute, ...]
    children: tuple[CanonicalNode, ...]
    spread_children: bool
    digest: str
    node_count: int
    attribute_count: int
    max_depth: int


@dataclass(frozen=True)
class TemplateContractSnapshot:
    """Non-DOM contract that must not be weakened by compression."""

    template_id: str
    provider_id: str | None
    business_id: str | None
    capability_id: str | None
    supported_card_sizes: tuple[str, ...]
    primary_data: tuple[str, ...]
    secondary_data: tuple[str, ...]
    optional_data: tuple[str, ...]
    action_policy: str
    requires_layout_action: bool


@dataclass(frozen=True)
class CanonicalTemplate:
    """One selectable Template variant and its immutable contract snapshot."""

    template_id: str
    variant_size: str
    root: CanonicalNode
    contract: TemplateContractSnapshot


@dataclass(frozen=True)
class SubtreeOccurrence:
    template_id: str
    variant_size: str
    path: tuple[int, ...]
    business_id: str | None
    node: CanonicalNode = field(compare=False, repr=False)


@dataclass(frozen=True)
class TreeDiff:
    """Human-readable edits; correctness does not depend on arbitrary weights."""

    inserted_nodes: int = 0
    deleted_nodes: int = 0
    changed_component_types: int = 0
    changed_parent_relations: int = 0
    changed_child_orders: int = 0
    added_attributes: int = 0
    deleted_attributes: int = 0
    changed_attributes: int = 0
    added_bindings: int = 0
    deleted_bindings: int = 0
    changed_bindings: int = 0
    contract_violations: int = 0

    def __add__(self, other: TreeDiff) -> TreeDiff:
        values = {
            name: getattr(self, name) + getattr(other, name)
            for name in self.__dataclass_fields__
        }
        return TreeDiff(**values)

    @property
    def structural_penalty(self) -> int:
        return (
            self.inserted_nodes
            + self.deleted_nodes
            + self.changed_component_types
            + self.changed_parent_relations
            + self.changed_child_orders
        )

    @property
    def attribute_penalty(self) -> int:
        return self.added_attributes + self.deleted_attributes + self.changed_attributes

    @property
    def binding_penalty(self) -> int:
        return self.added_bindings + self.deleted_bindings + self.changed_bindings

    def ordering_key(self) -> tuple[int, int, int, int]:
        """Prefer safe paths, then fewer structure, binding, and attribute edits."""
        return (
            self.contract_violations,
            self.structural_penalty,
            self.binding_penalty,
            self.attribute_penalty,
        )


@dataclass(frozen=True)
class PatternParameter:
    name: str
    path: tuple[int, ...]
    attribute_path: str
    value_type: str
    required: bool
    left_value: str | int | float | bool | None
    right_value: str | int | float | bool | None


@dataclass(frozen=True)
class PatternAttribute:
    attribute: CanonicalAttribute | None = None
    parameter_name: str | None = None


@dataclass(frozen=True)
class PatternNode:
    component: str
    attributes: tuple[tuple[str, PatternAttribute], ...]
    children: tuple[PatternNode, ...]
    spread_children: bool


@dataclass(frozen=True)
class ParameterizedPattern:
    pattern_id: str
    root: PatternNode
    parameters: tuple[PatternParameter, ...]
    left_values: dict[str, CanonicalAttribute]
    right_values: dict[str, CanonicalAttribute]


@dataclass(frozen=True)
class ExactCandidate:
    candidate_id: str
    digest: str
    component: str
    node_count: int
    attribute_count: int
    occurrence_count: int
    estimated_saving: int
    occurrences: tuple[SubtreeOccurrence, ...]


@dataclass(frozen=True)
class SimilarCandidate:
    candidate_id: str
    left: SubtreeOccurrence
    right: SubtreeOccurrence
    diff: TreeDiff
    normalized_structural_penalty: float
    normalized_attribute_penalty: float
    estimated_saving: int
    admissible: bool
    rejection_reasons: tuple[str, ...]
    pattern: ParameterizedPattern | None = field(compare=False, repr=False)


@dataclass(frozen=True)
class CompressionReport:
    report_version: Literal["template-compression-report/1"]
    semantic_similarity_enabled: bool
    template_count: int
    subtree_count: int
    exact_candidates: tuple[ExactCandidate, ...]
    similar_candidates: tuple[SimilarCandidate, ...]

    def to_json_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for candidate in payload["exact_candidates"]:
            for occurrence in candidate["occurrences"]:
                occurrence.pop("node", None)
        for candidate in payload["similar_candidates"]:
            candidate["left"].pop("node", None)
            candidate["right"].pop("node", None)
            candidate.pop("pattern", None)
        return payload


@dataclass(frozen=True)
class CompressionConfig:
    min_exact_occurrences: int = 2
    min_exact_nodes: int = 3
    min_similar_nodes: int = 3
    max_structural_penalty_ratio: float = 0.25
    max_attribute_penalty_ratio: float = 0.40
    min_estimated_saving: int = 1
    max_candidate_pairs: int = 20_000
    compare_across_businesses: bool = True
    semantic_similarity_enabled: bool = False


@dataclass(frozen=True)
class InterningMetrics:
    template_count: int
    node_occurrences: int
    unique_nodes_before: int
    unique_nodes_after: int
    shared_node_references: int
    node_reduction_ratio: float


_DAG_ARTIFACT_VERSION = "compressed-provider-component-dag/1"


class TemplateCompressionAnalyzer:
    """Analyze trusted Template IR without editing source assets."""

    def __init__(self, config: CompressionConfig | None = None) -> None:
        self.config = config or CompressionConfig()
        if self.config.semantic_similarity_enabled:
            raise ValueError("semantic similarity is reserved but not implemented")

    def analyze(self, definitions: Iterable[TemplateDefinition]) -> CompressionReport:
        templates = tuple(
            canonical
            for definition in definitions
            for canonical in canonicalize_definition(definition)
        )
        occurrences = tuple(
            occurrence
            for template in templates
            for occurrence in enumerate_subtrees(template)
        )
        exact = self._exact_candidates(occurrences)
        similar = self._similar_candidates(occurrences)
        return CompressionReport(
            report_version="template-compression-report/1",
            semantic_similarity_enabled=False,
            template_count=len(templates),
            subtree_count=len(occurrences),
            exact_candidates=exact,
            similar_candidates=similar,
        )

    def _exact_candidates(
        self,
        occurrences: tuple[SubtreeOccurrence, ...],
    ) -> tuple[ExactCandidate, ...]:
        by_digest: dict[str, list[SubtreeOccurrence]] = defaultdict(list)
        for occurrence in occurrences:
            by_digest[occurrence.node.digest].append(occurrence)
        candidates: list[ExactCandidate] = []
        for digest, items in by_digest.items():
            representative = items[0].node
            if len(items) < self.config.min_exact_occurrences:
                continue
            if representative.node_count < self.config.min_exact_nodes:
                continue
            saving = _exact_saving(representative.node_count, len(items))
            if saving < self.config.min_estimated_saving:
                continue
            candidate_id = f"exact-{representative.component.lower()}-{digest[:12]}"
            candidates.append(
                ExactCandidate(
                    candidate_id=candidate_id,
                    digest=digest,
                    component=representative.component,
                    node_count=representative.node_count,
                    attribute_count=representative.attribute_count,
                    occurrence_count=len(items),
                    estimated_saving=saving,
                    occurrences=tuple(items),
                )
            )
        return tuple(
            sorted(
                candidates,
                key=lambda candidate: (
                    -candidate.estimated_saving,
                    -candidate.node_count,
                    candidate.candidate_id,
                ),
            )
        )

    def _similar_candidates(
        self,
        occurrences: tuple[SubtreeOccurrence, ...],
    ) -> tuple[SimilarCandidate, ...]:
        buckets: dict[tuple[Any, ...], list[SubtreeOccurrence]] = defaultdict(list)
        for occurrence in occurrences:
            node = occurrence.node
            if node.node_count < self.config.min_similar_nodes:
                continue
            buckets[
                _coarse_signature(
                    occurrence,
                    compare_across_businesses=self.config.compare_across_businesses,
                )
            ].append(occurrence)
        candidates: list[SimilarCandidate] = []
        pair_count = 0
        for bucket in buckets.values():
            unique = _unique_digest_occurrences(bucket)
            for left_index, left in enumerate(unique):
                for right in unique[left_index + 1 :]:
                    pair_count += 1
                    if pair_count > self.config.max_candidate_pairs:
                        return _sort_similar_candidates(candidates)
                    candidate = self._build_similar_candidate(left, right)
                    if candidate is not None:
                        candidates.append(candidate)
        return _sort_similar_candidates(candidates)

    def _build_similar_candidate(
        self,
        left: SubtreeOccurrence,
        right: SubtreeOccurrence,
    ) -> SimilarCandidate | None:
        if left.node.digest == right.node.digest:
            return None
        diff = compare_trees(left.node, right.node)
        structural_denominator = max(1, left.node.node_count + right.node.node_count)
        attribute_denominator = max(
            1,
            left.node.attribute_count + right.node.attribute_count,
        )
        structural_ratio = diff.structural_penalty / structural_denominator
        attribute_ratio = diff.attribute_penalty / attribute_denominator
        pattern = anti_unify(left.node, right.node)
        saving = _pattern_saving(left.node, right.node, pattern)
        reasons = _candidate_rejection_reasons(
            diff,
            structural_ratio,
            attribute_ratio,
            saving,
            pattern,
            self.config,
        )
        candidate_id = (
            f"similar-{left.node.component.lower()}-"
            f"{left.node.digest[:6]}-{right.node.digest[:6]}"
        )
        return SimilarCandidate(
            candidate_id=candidate_id,
            left=left,
            right=right,
            diff=diff,
            normalized_structural_penalty=round(structural_ratio, 6),
            normalized_attribute_penalty=round(attribute_ratio, 6),
            estimated_saving=saving,
            admissible=not reasons,
            rejection_reasons=reasons,
            pattern=pattern,
        )


def intern_template_definitions(
    definitions: tuple[TemplateDefinition, ...],
) -> tuple[tuple[TemplateDefinition, ...], InterningMetrics]:
    """Hash-cons all TemplateNode values while preserving every Definition exactly."""
    cache: dict[str, TemplateNode] = {}
    total_nodes = 0
    original_object_ids: set[int] = set()

    def intern_node(node: TemplateNode) -> tuple[TemplateNode, str]:
        nonlocal total_nodes
        total_nodes += 1
        original_object_ids.add(id(node))
        child_results = tuple(intern_node(child) for child in node.children)
        children = tuple(result[0] for result in child_results)
        child_digests = tuple(result[1] for result in child_results)
        digest = _template_node_digest(node, child_digests)
        existing = cache.get(digest)
        if existing is not None:
            candidate = node.model_copy(update={"children": children})
            if existing != candidate:
                raise ValueError("Template Node hash collision")
            return existing, digest
        interned = node.model_copy(update={"children": children})
        cache[digest] = interned
        return interned, digest

    interned_definitions: list[TemplateDefinition] = []
    for definition in definitions:
        variants = tuple(
            variant.model_copy(update={"root": intern_node(variant.root)[0]})
            for variant in definition.variants
        )
        interned = definition.model_copy(update={"variants": variants})
        if interned.model_dump(by_alias=True) != definition.model_dump(by_alias=True):
            raise ValueError(f"Interned Template changed its value: {definition.wire_id}")
        interned_definitions.append(interned)
    unique_after = len(cache)
    shared_references = total_nodes - unique_after
    ratio = shared_references / total_nodes if total_nodes else 0.0
    metrics = InterningMetrics(
        template_count=len(definitions),
        node_occurrences=total_nodes,
        unique_nodes_before=len(original_object_ids),
        unique_nodes_after=unique_after,
        shared_node_references=shared_references,
        node_reduction_ratio=round(ratio, 6),
    )
    return tuple(interned_definitions), metrics


def template_interning_metrics(
    definitions: tuple[TemplateDefinition, ...],
) -> InterningMetrics:
    """Measure the value-preserving DAG reduction without retaining the result."""
    _interned, metrics = intern_template_definitions(definitions)
    return metrics


def serialize_compressed_component_dag(
    definitions: tuple[TemplateDefinition, ...],
) -> dict[str, Any]:
    """Serialize the lossless shared Provider DAG for inspection or offline loading."""
    interned_definitions, metrics = intern_template_definitions(definitions)
    nodes: dict[str, dict[str, Any]] = {}

    def serialize_node(node: TemplateNode) -> str:
        child_node_ids = tuple(serialize_node(child) for child in node.children)
        node_id = _template_node_digest(node, child_node_ids)
        payload = {
            "component": node.component,
            "values": [value.model_dump(by_alias=True) for value in node.values],
            "childNodeIds": list(child_node_ids),
            "spreadChildren": node.spread_children,
        }
        existing = nodes.get(node_id)
        if existing is not None and existing != payload:
            raise ValueError("Compressed DAG node ID collision")
        nodes[node_id] = payload
        return node_id

    templates: list[dict[str, Any]] = []
    for definition in interned_definitions:
        payload = definition.model_dump(by_alias=True)
        variants = payload.get("variants")
        if not isinstance(variants, (list, tuple)):
            raise ValueError(f"Template has no serializable variants: {definition.wire_id}")
        for variant, source_variant in zip(variants, definition.variants, strict=True):
            variant.pop("root", None)
            variant["rootNodeId"] = serialize_node(source_variant.root)
        templates.append(payload)
    return {
        "artifactVersion": _DAG_ARTIFACT_VERSION,
        "sourceFormat": "cardtpl/1",
        "metrics": asdict(metrics),
        "nodes": nodes,
        "templates": templates,
    }


def write_compressed_component_dag(
    definitions: tuple[TemplateDefinition, ...],
    output_path: Path,
) -> None:
    """Write a deterministic, human-inspectable compressed Provider DAG."""
    payload = serialize_compressed_component_dag(definitions)
    content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")


def canonicalize_definition(definition: TemplateDefinition) -> tuple[CanonicalTemplate, ...]:
    """Convert every Template variant into an immutable canonical tree."""
    contract = TemplateContractSnapshot(
        template_id=definition.wire_id,
        provider_id=definition.provider_id,
        business_id=definition.business_id,
        capability_id=definition.capability_id,
        supported_card_sizes=tuple(
            size
            for variant in definition.variants
            for size in variant.supported_card_sizes
        ),
        primary_data=definition.primary_data,
        secondary_data=definition.secondary_data,
        optional_data=definition.optional_data,
        action_policy=definition.action_policy,
        requires_layout_action=definition.requires_layout_action,
    )
    return tuple(
        CanonicalTemplate(
            template_id=definition.wire_id,
            variant_size=variant.size,
            root=_canonicalize_node(definition, variant, variant.root),
            contract=contract,
        )
        for variant in definition.variants
    )


def enumerate_subtrees(template: CanonicalTemplate) -> tuple[SubtreeOccurrence, ...]:
    occurrences: list[SubtreeOccurrence] = []

    def visit(node: CanonicalNode, path: tuple[int, ...]) -> None:
        occurrences.append(
            SubtreeOccurrence(
                template_id=template.template_id,
                variant_size=template.variant_size,
                path=path,
                business_id=template.contract.business_id,
                node=node,
            )
        )
        for index, child in enumerate(node.children):
            visit(child, (*path, index))

    visit(template.root, ())
    return tuple(occurrences)


def compare_trees(left: CanonicalNode, right: CanonicalNode) -> TreeDiff:
    """Return the lexicographically cheapest ordered-tree edit description."""
    component_diff = TreeDiff(
        changed_component_types=int(left.component != right.component),
        contract_violations=int(_forbidden_component_change(left, right)),
    )
    attribute_diff = _compare_attributes(left.attributes, right.attributes)
    child_diff = _compare_child_sequences(left.children, right.children)
    return component_diff + attribute_diff + child_diff


def anti_unify(left: CanonicalNode, right: CanonicalNode) -> ParameterizedPattern | None:
    """Create an exact-expandable Pattern for trees with identical topology."""
    parameters: list[PatternParameter] = []
    left_values: dict[str, CanonicalAttribute] = {}
    right_values: dict[str, CanonicalAttribute] = {}

    def unify_node(
        left_node: CanonicalNode,
        right_node: CanonicalNode,
        node_path: tuple[int, ...],
    ) -> PatternNode | None:
        same_shape = (
            left_node.component == right_node.component
            and len(left_node.children) == len(right_node.children)
            and left_node.spread_children == right_node.spread_children
        )
        if not same_shape:
            return None
        left_attrs = {attribute.path: attribute for attribute in left_node.attributes}
        right_attrs = {attribute.path: attribute for attribute in right_node.attributes}
        if set(left_attrs) != set(right_attrs):
            return None
        pattern_attributes: list[tuple[str, PatternAttribute]] = []
        for attribute_path in sorted(left_attrs):
            left_attr = left_attrs[attribute_path]
            right_attr = right_attrs[attribute_path]
            if left_attr == right_attr:
                pattern_attributes.append(
                    (attribute_path, PatternAttribute(attribute=left_attr))
                )
                continue
            if not _attributes_parameterizable(left_attr, right_attr):
                return None
            parameter_name = f"p{len(parameters) + 1}"
            parameter = PatternParameter(
                name=parameter_name,
                path=node_path,
                attribute_path=attribute_path,
                value_type=left_attr.value_type,
                required=left_attr.required,
                left_value=left_attr.value,
                right_value=right_attr.value,
            )
            parameters.append(parameter)
            left_values[parameter_name] = left_attr
            right_values[parameter_name] = right_attr
            pattern_attributes.append(
                (attribute_path, PatternAttribute(parameter_name=parameter_name))
            )
        children: list[PatternNode] = []
        for index, (left_child, right_child) in enumerate(
            zip(left_node.children, right_node.children, strict=True)
        ):
            child = unify_node(left_child, right_child, (*node_path, index))
            if child is None:
                return None
            children.append(child)
        return PatternNode(
            component=left_node.component,
            attributes=tuple(pattern_attributes),
            children=tuple(children),
            spread_children=left_node.spread_children,
        )

    root = unify_node(left, right, ())
    if root is None or not parameters:
        return None
    seed = f"{left.digest}:{right.digest}:{len(parameters)}"
    pattern_id = f"pattern-{left.component.lower()}-{_short_digest(seed)}"
    pattern = ParameterizedPattern(
        pattern_id=pattern_id,
        root=root,
        parameters=tuple(parameters),
        left_values=left_values,
        right_values=right_values,
    )
    if expand_pattern(pattern, pattern.left_values).digest != left.digest:
        return None
    if expand_pattern(pattern, pattern.right_values).digest != right.digest:
        return None
    return pattern


def expand_pattern(
    pattern: ParameterizedPattern,
    values: dict[str, CanonicalAttribute],
) -> CanonicalNode:
    """Expand one Pattern using a closed parameter assignment."""
    expected = {parameter.name for parameter in pattern.parameters}
    if set(values) != expected:
        raise ValueError("Pattern parameter assignment is incomplete")

    def expand_node(node: PatternNode) -> CanonicalNode:
        attributes: list[CanonicalAttribute] = []
        for _path, pattern_attribute in node.attributes:
            if pattern_attribute.attribute is not None:
                attributes.append(pattern_attribute.attribute)
                continue
            parameter_name = pattern_attribute.parameter_name
            if parameter_name is None:
                raise ValueError("Pattern attribute has no value")
            attributes.append(values[parameter_name])
        children = tuple(expand_node(child) for child in node.children)
        return _build_canonical_node(
            node.component,
            tuple(attributes),
            children,
            node.spread_children,
        )

    return expand_node(pattern.root)


def validate_component_dag(dependencies: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    """Return a topological order or reject cyclic/missing dependencies."""
    known = set(dependencies)
    referenced = {dependency for items in dependencies.values() for dependency in items}
    missing = sorted(referenced - known)
    if missing:
        raise ValueError(f"Component DAG references unknown nodes: {', '.join(missing)}")
    incoming = {node_id: 0 for node_id in known}
    consumers: dict[str, list[str]] = defaultdict(list)
    for node_id, items in dependencies.items():
        for dependency in items:
            incoming[node_id] += 1
            consumers[dependency].append(node_id)
    ready = sorted(node_id for node_id, count in incoming.items() if count == 0)
    order: list[str] = []
    while ready:
        node_id = ready.pop(0)
        order.append(node_id)
        for consumer in sorted(consumers[node_id]):
            incoming[consumer] -= 1
            if incoming[consumer] == 0:
                ready.append(consumer)
                ready.sort()
    if len(order) != len(known):
        raise ValueError("Component DAG contains a cycle")
    return tuple(order)


def _canonicalize_node(
    definition: TemplateDefinition,
    variant: TemplateVariant,
    node: TemplateNode,
) -> CanonicalNode:
    attributes = tuple(
        attribute
        for index, value in enumerate(node.values)
        for attribute in _flatten_value(
            definition,
            variant,
            value,
            f"value[{index}]",
        )
    )
    children = tuple(
        _canonicalize_node(definition, variant, child) for child in node.children
    )
    return _build_canonical_node(
        node.component,
        tuple(sorted(attributes, key=lambda attribute: attribute.path)),
        children,
        node.spread_children,
    )


def _flatten_value(
    definition: TemplateDefinition,
    variant: TemplateVariant,
    value: TemplateValue,
    path: str,
) -> tuple[CanonicalAttribute, ...]:
    if value.kind == "object":
        return tuple(
            attribute
            for key, child in sorted(value.properties.items())
            for attribute in _flatten_value(definition, variant, child, f"{path}.{key}")
        )
    if value.kind == "array":
        return tuple(
            attribute
            for index, child in enumerate(value.items)
            for attribute in _flatten_value(definition, variant, child, f"{path}[{index}]")
        )
    if value.kind == "binding":
        binding = definition.bindings.get(value.name or "")
        binding_path = binding.path if binding is not None else value.name
        binding_type = binding.data_type if binding is not None else "unknown"
        required = value.name in variant.required_bindings
        return (
            CanonicalAttribute(
                path=path,
                kind="binding",
                value=binding_path,
                value_type=binding_type,
                required=required,
            ),
        )
    if value.kind == "parameter":
        schema = variant.parameters_schema.get("properties", {}).get(value.name or "", {})
        value_type = schema.get("type", "unknown") if isinstance(schema, dict) else "unknown"
        required = value.name in variant.parameters_schema.get("required", ())
        return (
            CanonicalAttribute(
                path=path,
                kind="parameter",
                value=value.name,
                value_type=str(value_type),
                required=required,
            ),
        )
    value_type = _literal_type(value.value)
    return (
        CanonicalAttribute(
            path=path,
            kind=value.kind,
            value=value.value,
            value_type=value_type,
            required=True,
        ),
    )


def _build_canonical_node(
    component: str,
    attributes: tuple[CanonicalAttribute, ...],
    children: tuple[CanonicalNode, ...],
    spread_children: bool,
) -> CanonicalNode:
    payload = {
        "component": component,
        "attributes": [attribute.identity() for attribute in attributes],
        "children": [child.digest for child in children],
        "spreadChildren": spread_children,
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return CanonicalNode(
        component=component,
        attributes=attributes,
        children=children,
        spread_children=spread_children,
        digest=digest,
        node_count=1 + sum(child.node_count for child in children),
        attribute_count=len(attributes) + sum(child.attribute_count for child in children),
        max_depth=1 + max((child.max_depth for child in children), default=0),
    )


def _template_node_digest(
    node: TemplateNode,
    child_digests: tuple[str, ...],
) -> str:
    payload = {
        "component": node.component,
        "values": [value.model_dump(by_alias=True) for value in node.values],
        "children": child_digests,
        "spreadChildren": node.spread_children,
    }
    content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _compare_attributes(
    left: tuple[CanonicalAttribute, ...],
    right: tuple[CanonicalAttribute, ...],
) -> TreeDiff:
    left_by_path = {attribute.path: attribute for attribute in left}
    right_by_path = {attribute.path: attribute for attribute in right}
    left_paths = set(left_by_path)
    right_paths = set(right_by_path)
    deleted = left_paths - right_paths
    added = right_paths - left_paths
    diff = TreeDiff(
        added_attributes=len(added),
        deleted_attributes=len(deleted),
        added_bindings=sum(right_by_path[path].kind == "binding" for path in added),
        deleted_bindings=sum(left_by_path[path].kind == "binding" for path in deleted),
    )
    for path in sorted(left_paths & right_paths):
        left_attr = left_by_path[path]
        right_attr = right_by_path[path]
        if left_attr == right_attr:
            continue
        binding_change = left_attr.kind == "binding" or right_attr.kind == "binding"
        contract_violation = not _attributes_parameterizable(left_attr, right_attr)
        diff += TreeDiff(
            changed_attributes=1,
            changed_bindings=int(binding_change),
            contract_violations=int(contract_violation),
        )
    return diff


def _compare_child_sequences(
    left: tuple[CanonicalNode, ...],
    right: tuple[CanonicalNode, ...],
) -> TreeDiff:
    rows = len(left) + 1
    columns = len(right) + 1
    table: list[list[TreeDiff]] = [
        [TreeDiff() for _column in range(columns)] for _row in range(rows)
    ]
    for row in range(1, rows):
        table[row][0] = table[row - 1][0] + TreeDiff(
            deleted_nodes=left[row - 1].node_count
        )
    for column in range(1, columns):
        table[0][column] = table[0][column - 1] + TreeDiff(
            inserted_nodes=right[column - 1].node_count
        )
    for row in range(1, rows):
        for column in range(1, columns):
            delete = table[row - 1][column] + TreeDiff(
                deleted_nodes=left[row - 1].node_count
            )
            insert = table[row][column - 1] + TreeDiff(
                inserted_nodes=right[column - 1].node_count
            )
            replace = table[row - 1][column - 1] + compare_trees(
                left[row - 1],
                right[column - 1],
            )
            table[row][column] = min(
                (delete, insert, replace),
                key=TreeDiff.ordering_key,
            )
    return table[-1][-1]


def _attributes_parameterizable(
    left: CanonicalAttribute,
    right: CanonicalAttribute,
) -> bool:
    if left.kind not in _SAFE_PARAMETER_KINDS:
        return False
    if right.kind not in _SAFE_PARAMETER_KINDS:
        return False
    if left.kind != right.kind:
        return False
    if left.value_type != right.value_type:
        return False
    return left.required == right.required


def _forbidden_component_change(left: CanonicalNode, right: CanonicalNode) -> bool:
    if left.component == right.component:
        return False
    protected = left.component in _FORBIDDEN_COMPONENT_REPLACEMENTS
    protected = protected or right.component in _FORBIDDEN_COMPONENT_REPLACEMENTS
    return protected or bool(left.spread_children or right.spread_children)


def _coarse_signature(
    occurrence: SubtreeOccurrence,
    *,
    compare_across_businesses: bool,
) -> tuple[Any, ...]:
    node = occurrence.node
    component_counts = Counter(item.component for item in _walk_nodes(node))
    size_bucket = node.node_count // 3
    business_bucket = occurrence.business_id
    if compare_across_businesses and occurrence.business_id:
        business_bucket = "__business__"
    if occurrence.business_id is None:
        business_bucket = "__layout__"
    return (
        node.component,
        size_bucket,
        min(node.max_depth, 6),
        tuple(sorted(component_counts.items())),
        business_bucket,
    )


def _walk_nodes(node: CanonicalNode) -> Iterable[CanonicalNode]:
    yield node
    for child in node.children:
        yield from _walk_nodes(child)


def _unique_digest_occurrences(
    occurrences: list[SubtreeOccurrence],
) -> tuple[SubtreeOccurrence, ...]:
    result: dict[str, SubtreeOccurrence] = {}
    for occurrence in occurrences:
        result.setdefault(occurrence.node.digest, occurrence)
    return tuple(result.values())


def _candidate_rejection_reasons(
    diff: TreeDiff,
    structural_ratio: float,
    attribute_ratio: float,
    saving: int,
    pattern: ParameterizedPattern | None,
    config: CompressionConfig,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if diff.contract_violations:
        reasons.append("contract-violation")
    if structural_ratio > config.max_structural_penalty_ratio:
        reasons.append("structure-penalty-too-high")
    if attribute_ratio > config.max_attribute_penalty_ratio:
        reasons.append("attribute-penalty-too-high")
    if pattern is None:
        reasons.append("no-exact-expandable-pattern")
    if saving < config.min_estimated_saving:
        reasons.append("saving-too-small")
    return tuple(reasons)


def _exact_saving(node_count: int, occurrences: int) -> int:
    definition_cost = node_count
    reference_cost = occurrences
    return occurrences * node_count - definition_cost - reference_cost


def _pattern_saving(
    left: CanonicalNode,
    right: CanonicalNode,
    pattern: ParameterizedPattern | None,
) -> int:
    if pattern is None:
        return 0
    original_cost = left.node_count + right.node_count
    definition_cost = max(left.node_count, right.node_count)
    reference_cost = 2 + len(pattern.parameters) * 2
    return original_cost - definition_cost - reference_cost


def _sort_similar_candidates(
    candidates: list[SimilarCandidate],
) -> tuple[SimilarCandidate, ...]:
    return tuple(
        sorted(
            candidates,
            key=lambda candidate: (
                not candidate.admissible,
                -candidate.estimated_saving,
                candidate.diff.ordering_key(),
                candidate.candidate_id,
            ),
        )
    )


def _literal_type(value: str | int | float | bool | None) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return "string"


def _short_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
