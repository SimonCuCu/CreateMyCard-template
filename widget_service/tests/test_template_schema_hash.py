from __future__ import annotations

from typing import Any

from models.generation import CandidateDataBinding, TaskSpec
from services.template_generation.engine.advanced.data_shape import extract_data_shape
from services.template_generation.engine.advanced.scope_planner import (
    plan_template_route_with_hash,
)
from services.template_generation.engine.cardplan.registry import get_cardplan_registry
from services.template_generation.schema_hash import (
    compute_schema_fingerprint,
    schemas_have_matching_structure,
)


def _field(value: Any, field_type: str = "string") -> dict[str, Any]:
    return {
        "type": field_type,
        "description": "runtime sample",
        "sampleValue": value,
    }


def _weather_task() -> TaskSpec:
    return TaskSpec(
        userQuery="天气",
        size="2x2",
        dataModelSchema={
            "data": {
                "weather": {
                    "location": {"districtName": _field("青浦区")},
                    "current": {
                        "temperatureText": _field("29°C"),
                        "condition": _field("多云"),
                        "airQuality": _field("良"),
                    },
                    "daily": [{"temperatureRangeText": _field("25° / 32°")}],
                }
            }
        },
    )


def _weather_binding() -> CandidateDataBinding:
    return CandidateDataBinding(
        capabilityId="ViewWeather",
        writeResultTo="/data/weather",
        candidateOutputFields=[
            "/location/districtName",
            "/current/temperatureText",
            "/current/condition",
            "/current/airQuality",
            "/daily/0/temperatureRangeText",
        ],
    )


def _weather_card_spec() -> dict[str, Any]:
    return {
        "title": "今日天气",
        "suggestSize": "2x2",
        "dataBindings": [
            {
                "capabilityId": "ViewWeather",
                "writeResultTo": "/data/weather",
            }
        ],
    }


def test_schema_hash_ignores_samples_descriptions_and_key_order() -> None:
    left = {
        "city": _field("北京"),
        "temperature": _field(26, "number"),
    }
    right = {
        "temperature": {
            "type": "number",
            "description": "changed",
            "sampleValue": -3,
        },
        "city": _field("深圳"),
    }

    assert compute_schema_fingerprint(left) == compute_schema_fingerprint(right)


def test_provider_schema_matches_runtime_subset_by_hash() -> None:
    provider = {
        "type": "object",
        "properties": {
            "city": {"type": "string"},
            "temperature": {"type": "number"},
        },
    }
    runtime = {"city": _field("深圳")}

    matched, fingerprint = schemas_have_matching_structure(provider, runtime)

    assert matched is True
    assert fingerprint is not None
    assert fingerprint.structure_hash


def test_provider_schema_rejects_runtime_type_change() -> None:
    provider = {
        "type": "object",
        "properties": {"temperature": {"type": "number"}},
    }

    matched, fingerprint = schemas_have_matching_structure(
        provider,
        {"temperature": _field("26°C")},
    )

    assert matched is False
    assert fingerprint is None


def test_weather_schema_hash_selects_the_provider_component() -> None:
    task_spec = _weather_task()
    decision = plan_template_route_with_hash(
        task_spec,
        extract_data_shape(task_spec),
        get_cardplan_registry(),
        (_weather_binding(),),
        ("ViewWeather",),
        _weather_card_spec(),
    )

    assert decision.matched is True
    assert decision.scope is not None
    assert decision.scope.advanced_component_ids == ("WeatherOverview",)


def test_hash_miss_requests_first_layer_fallback() -> None:
    task_spec = _weather_task()
    task_spec.dataModelSchema["data"]["weather"]["unknown"] = _field("extra")

    decision = plan_template_route_with_hash(
        task_spec,
        extract_data_shape(task_spec),
        get_cardplan_registry(),
        (_weather_binding(),),
        ("ViewWeather",),
        _weather_card_spec(),
    )

    assert decision.matched is False
    assert decision.reason == "not_found"
