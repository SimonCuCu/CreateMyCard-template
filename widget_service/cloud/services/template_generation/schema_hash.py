"""Versioned structural fingerprints for Provider and TaskSpec schemas."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

SCHEMA_FINGERPRINT_VERSION = 1
_SCALAR_TYPES = frozenset({"boolean", "integer", "null", "number", "string"})


@dataclass(frozen=True)
class SchemaFingerprint:
    version: int
    canonical_schema: str
    structure_hash: str


@dataclass(frozen=True)
class HashRouteDecision:
    matched: bool
    scope: Any | None = None
    reason: str = "not_found"
    candidate_count: int = 0
    fingerprint_version: int = SCHEMA_FINGERPRINT_VERSION


def _schema_structure(value: Any) -> dict[str, Any]:
    if isinstance(value, list):
        if not value:
            raise ValueError("runtime schema arrays must not be empty")
        items = [_schema_structure(item) for item in value]
        if any(item != items[0] for item in items[1:]):
            raise ValueError("runtime schema arrays must be structurally homogeneous")
        return {"type": "array", "items": items[0]}
    if not isinstance(value, dict):
        raise ValueError("schema nodes must be objects")
    schema_type = value.get("type")
    if schema_type in _SCALAR_TYPES:
        return {"type": schema_type}
    if schema_type == "array":
        return {"type": "array", "items": _schema_structure(value.get("items"))}
    if schema_type == "object":
        properties = value.get("properties")
        if not isinstance(properties, dict):
            raise ValueError("object schemas must declare properties")
        return {
            "type": "object",
            "properties": {key: _schema_structure(properties[key]) for key in sorted(properties)},
        }
    if isinstance(schema_type, list):
        normalized = sorted(item for item in schema_type if isinstance(item, str))
        if not normalized:
            raise ValueError("schema type union must not be empty")
        return {"type": normalized}
    structural_keys = {
        key: child
        for key, child in value.items()
        if key not in {"description", "sampleValue", "title", "default", "examples"}
    }
    if not structural_keys:
        raise ValueError("schema object has no structural fields")
    return {
        "type": "object",
        "properties": {
            key: _schema_structure(structural_keys[key]) for key in sorted(structural_keys)
        },
    }


def _project_structure(candidate: dict[str, Any], observed: dict[str, Any]) -> dict[str, Any]:
    if candidate.get("type") != observed.get("type"):
        raise ValueError("schema types do not match")
    schema_type = observed["type"]
    if schema_type == "object":
        candidate_properties = candidate.get("properties")
        observed_properties = observed.get("properties")
        if not isinstance(candidate_properties, dict) or not isinstance(observed_properties, dict):
            raise ValueError("object schema properties are invalid")
        if not set(observed_properties).issubset(candidate_properties):
            raise ValueError("runtime schema contains fields absent from Provider schema")
        return {
            "type": "object",
            "properties": {
                key: _project_structure(candidate_properties[key], observed_properties[key])
                for key in sorted(observed_properties)
            },
        }
    if schema_type == "array":
        return {
            "type": "array",
            "items": _project_structure(candidate["items"], observed["items"]),
        }
    return candidate


def _fingerprint(structure: dict[str, Any]) -> SchemaFingerprint:
    canonical = json.dumps(structure, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    prefix = f"provider-data-schema:v{SCHEMA_FINGERPRINT_VERSION}\0"
    digest = hashlib.sha256((prefix + canonical).encode("utf-8")).hexdigest()
    return SchemaFingerprint(SCHEMA_FINGERPRINT_VERSION, canonical, digest)


def compute_schema_fingerprint(schema: dict[str, Any]) -> SchemaFingerprint:
    """Compute a stable fingerprint while ignoring samples and descriptions."""

    return _fingerprint(_schema_structure(schema))


def schemas_have_matching_structure(
    provider_schema: dict[str, Any],
    runtime_schema: dict[str, Any],
) -> tuple[bool, SchemaFingerprint | None]:
    """Compare runtime fields with the same projection of a Provider schema."""

    try:
        observed = _schema_structure(runtime_schema)
        projected = _project_structure(_schema_structure(provider_schema), observed)
        observed_fingerprint = _fingerprint(observed)
        provider_fingerprint = _fingerprint(projected)
    except (KeyError, TypeError, ValueError):
        return False, None
    matched = (
        observed_fingerprint.version == provider_fingerprint.version
        and observed_fingerprint.structure_hash == provider_fingerprint.structure_hash
        and observed_fingerprint.canonical_schema == provider_fingerprint.canonical_schema
    )
    return matched, observed_fingerprint if matched else None
