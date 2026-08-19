"""Precomputed CardTpl Variant records for exact in-memory retrieval."""

from __future__ import annotations

from dataclasses import dataclass

from .models import TemplateDefinition


@dataclass(frozen=True, order=True)
class FieldToken:
    capability_id: str
    path: str
    data_type: str


@dataclass(frozen=True)
class TemplateVariantSearchRecord:
    capability_id: str
    compatible_theme_ids: frozenset[str]
    template_id: str
    variant_name: str
    supported_card_sizes: frozenset[str]
    supported_roles: frozenset[str]
    required_field_tokens: frozenset[FieldToken]


def build_template_variant_search_records(
    templates: dict[str, TemplateDefinition],
) -> tuple[TemplateVariantSearchRecord, ...]:
    records: list[TemplateVariantSearchRecord] = []
    for definition in templates.values():
        capability_id = definition.capability_id
        if definition.source_format != "cardtpl/1" or capability_id is None:
            continue
        for variant in definition.variants:
            records.append(
                TemplateVariantSearchRecord(
                    capability_id=capability_id,
                    compatible_theme_ids=frozenset(definition.compatible_theme_profile_ids),
                    template_id=definition.wire_id,
                    variant_name=variant.size,
                    supported_card_sizes=frozenset(variant.supported_card_sizes),
                    supported_roles=frozenset(variant.supported_roles),
                    required_field_tokens=frozenset(
                        FieldToken(capability_id, field.path, field.data_type)
                        for field in variant.required_data_fields
                    ),
                )
            )
    return tuple(records)
