"""Deterministic CardTpl Variant retrieval from LLM-extracted field requirements."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from models.generation import CandidateDataBinding, TaskSpec
from services.template_generation.engine.advanced.models import TemplateRetrievalQuery

from .provider_bundle import provider_template_variant_admission
from .registry import CardPlanRegistry
from .retrieval_index import FieldToken, TemplateVariantSearchRecord


class TemplateRetrievalMiss(ValueError):
    """No single CardTpl Variant can satisfy the extracted requirement set."""


@dataclass(frozen=True)
class TemplateMatch:
    theme_id: str
    template_id: str
    variant_name: str


def retrieve_template_variant(
    query: TemplateRetrievalQuery,
    task_spec: TaskSpec,
    registry: CardPlanRegistry,
    coverage_bindings: tuple[CandidateDataBinding, ...],
    card_spec: dict[str, Any],
) -> TemplateMatch:
    """Return one stable minimum-superset Variant or raise an explicit miss."""

    registry.require_theme(query.theme_id)
    groups = query.required_output_fields_by_capability
    if len(groups) != 1:
        raise TemplateRetrievalMiss("template retrieval requires exactly one capability")
    capability_id, paths = next(iter(groups.items()))
    if not paths:
        raise TemplateRetrievalMiss("template retrieval requires non-empty output fields")
    candidate_paths = _candidate_paths(coverage_bindings, capability_id)
    if not set(paths).issubset(candidate_paths):
        raise TemplateRetrievalMiss("required output fields must come from candidates")
    data_root = _capability_data_root(card_spec, capability_id)
    query_tokens = frozenset(
        _task_spec_field_token(task_spec, data_root, capability_id, path) for path in paths
    )
    matches = [
        record
        for record in registry.template_variant_search_records
        if _record_matches(
            record,
            query,
            query_tokens,
            task_spec,
            registry,
            card_spec,
        )
    ]
    if not matches:
        raise TemplateRetrievalMiss("no CardTpl Variant contains every required output field")
    selected = min(
        matches,
        key=lambda record: (
            len(record.required_field_tokens - query_tokens),
            record.template_id,
            record.variant_name,
        ),
    )
    return TemplateMatch(query.theme_id, selected.template_id, selected.variant_name)


def _candidate_paths(
    coverage_bindings: tuple[CandidateDataBinding, ...],
    capability_id: str,
) -> set[str]:
    matching = [item for item in coverage_bindings if item.capabilityId == capability_id]
    if len(matching) != 1:
        raise TemplateRetrievalMiss("template retrieval requires one binding per capability")
    return set(matching[0].candidateOutputFields)


def _capability_data_root(card_spec: dict[str, Any], capability_id: str) -> str:
    raw_bindings = card_spec.get("dataBindings")
    if not isinstance(raw_bindings, list):
        raise TemplateRetrievalMiss("CardSpec data bindings are unavailable")
    roots = {
        item.get("writeResultTo")
        for item in raw_bindings
        if isinstance(item, dict) and item.get("capabilityId") == capability_id
    }
    valid_roots = {
        root
        for root in roots
        if isinstance(root, str) and (root == "/data" or root.startswith("/data/"))
    }
    if len(valid_roots) != 1:
        raise TemplateRetrievalMiss("capability data root is unavailable or ambiguous")
    return next(iter(valid_roots))


def _task_spec_field_token(
    task_spec: TaskSpec,
    data_root: str,
    capability_id: str,
    relative_path: str,
) -> FieldToken:
    pointer = f"{data_root.rstrip('/')}{relative_path}"
    leaf = _task_spec_schema_leaf(task_spec.dataModelSchema, pointer)
    if leaf is None:
        raise TemplateRetrievalMiss(
            f"required output field is absent from TaskSpec: {relative_path}"
        )
    data_type = leaf.get("type")
    if not isinstance(data_type, str):
        raise TemplateRetrievalMiss(f"required output field has no type: {relative_path}")
    return FieldToken(capability_id, relative_path, data_type)


def _task_spec_schema_leaf(schema: dict[str, Any], pointer: str) -> dict[str, Any] | None:
    current: Any = schema
    for raw_part in pointer.removeprefix("/").split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            current = current.get(part)
            continue
        if isinstance(current, list) and part == "0" and current:
            current = current[0]
            continue
        return None
    return current if isinstance(current, dict) else None


def _record_matches(
    record: TemplateVariantSearchRecord,
    query: TemplateRetrievalQuery,
    query_tokens: frozenset[FieldToken],
    task_spec: TaskSpec,
    registry: CardPlanRegistry,
    card_spec: dict[str, Any],
) -> bool:
    capability_matches = all(token.capability_id == record.capability_id for token in query_tokens)
    size_matches = task_spec.size in record.supported_card_sizes
    role_matches = "hero" in record.supported_roles
    # Theme compatibility is intentionally not a retrieval gate in this experiment.
    # Capability, fields, size, role and Provider admission remain hard constraints.
    basic_constraints_match = capability_matches and size_matches
    if not basic_constraints_match or not role_matches:
        return False
    if not query_tokens.issubset(record.required_field_tokens):
        return False
    definition = registry.require_template(record.template_id)
    variant = registry.require_variant(record.template_id, record.variant_name)
    admission = provider_template_variant_admission(definition, variant, task_spec, card_spec)
    return admission.admitted
