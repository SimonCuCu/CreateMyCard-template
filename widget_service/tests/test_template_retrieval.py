from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from models.generation import CandidateDataBinding, TaskSpec
from services.template_generation.engine.advanced.data_shape import extract_data_shape
from services.template_generation.engine.advanced.models import TemplateRetrievalQuery
from services.template_generation.engine.advanced.scope_planner import (
    build_template_retrieval_prompt,
)
from services.template_generation.engine.cardplan.registry import get_cardplan_registry
from services.template_generation.engine.cardplan.retrieval_index import FieldToken
from services.template_generation.engine.cardplan.template_retrieval import (
    TemplateRetrievalMiss,
    retrieve_template_variant,
)

_WEATHER_FIELDS = (
    "/location/districtName",
    "/current/temperatureText",
    "/current/condition",
    "/current/airQuality",
    "/daily/0/temperatureRangeText",
)


def _field(value: Any, data_type: str = "string") -> dict[str, Any]:
    return {
        "type": data_type,
        "description": "trusted test field",
        "sampleValue": value,
    }


def _weather_task() -> TaskSpec:
    return TaskSpec(
        userQuery="显示温度和天气情况",
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
        candidateOutputFields=list(_WEATHER_FIELDS),
    )


def _weather_card_spec() -> dict[str, Any]:
    return {
        "suggestSize": "2x2",
        "dataBindings": [
            {
                "capabilityId": "ViewWeather",
                "writeResultTo": "/data/weather",
            }
        ],
    }


def _query(*paths: str, theme_id: str = "family-weather-care-blue") -> TemplateRetrievalQuery:
    return TemplateRetrievalQuery(
        themeId=theme_id,
        requiredOutputFieldsByCapability={"ViewWeather": paths},
    )


def test_first_layer_contract_rejects_legacy_route_fields() -> None:
    with pytest.raises(ValidationError):
        TemplateRetrievalQuery.model_validate(
            {
                "routeVersion": "template-retrieval-query/1",
                "themeId": "family-weather-care-blue",
                "requiredOutputFieldsByCapability": {
                    "ViewWeather": ["/current/temperatureText"],
                },
                "templateUsable": True,
                "advancedComponentIds": ["WeatherOverview"],
            }
        )


def test_first_layer_prompt_does_not_expose_components_or_templates() -> None:
    task_spec = _weather_task()
    messages = build_template_retrieval_prompt(
        task_spec,
        extract_data_shape(task_spec),
        get_cardplan_registry(),
        {"ViewWeather": _WEATHER_FIELDS},
    )
    payload = json.loads(messages[1]["content"])

    assert "advancedComponents" not in payload
    assert "templates" not in payload
    assert "candidateOutputFieldsByCapability" in payload


def test_field_subset_returns_one_stable_template_variant_without_component() -> None:
    match = retrieve_template_variant(
        _query("/current/temperatureText", "/current/condition"),
        _weather_task(),
        get_cardplan_registry(),
        (_weather_binding(),),
        _weather_card_spec(),
    )

    assert match.template_id == "WeatherOverview@1"
    assert match.variant_name == "hero"
    assert not hasattr(match, "component_id")


def test_optional_fields_are_not_expanded_into_variant_combinations() -> None:
    registry = get_cardplan_registry()
    record = next(
        item
        for item in registry.template_variant_search_records
        if item.template_id == "HeartRateOverview@1" and item.variant_name == "hero"
    )

    assert FieldToken("GetHealthAndSportSummary", "/exerciseHeartRateAvg", "integer") in (
        record.required_field_tokens
    )
    assert all(token.path != "/updatedAt" for token in record.required_field_tokens)


def test_theme_mismatch_does_not_block_field_match() -> None:
    match = retrieve_template_variant(
        _query("/current/temperatureText", theme_id="meeting-paper-neutral"),
        _weather_task(),
        get_cardplan_registry(),
        (_weather_binding(),),
        _weather_card_spec(),
    )

    assert match.theme_id == "meeting-paper-neutral"
    assert match.template_id == "WeatherOverview@1"
    assert match.variant_name == "hero"


def test_task_spec_type_mismatch_is_a_retrieval_miss() -> None:
    task_spec = _weather_task()
    task_spec.dataModelSchema["data"]["weather"]["current"]["condition"] = _field(1, "number")

    with pytest.raises(TemplateRetrievalMiss, match="no CardTpl Variant"):
        retrieve_template_variant(
            _query("/current/condition"),
            task_spec,
            get_cardplan_registry(),
            (_weather_binding(),),
            _weather_card_spec(),
        )


def test_multiple_capabilities_are_rejected_without_combination_search() -> None:
    query = TemplateRetrievalQuery(
        themeId="family-weather-care-blue",
        requiredOutputFieldsByCapability={
            "ViewWeather": ("/current/temperatureText",),
            "GetPhoneBatteryInfo": ("/batterySOCText",),
        },
    )

    with pytest.raises(TemplateRetrievalMiss, match="exactly one capability"):
        retrieve_template_variant(
            query,
            _weather_task(),
            get_cardplan_registry(),
            (_weather_binding(),),
            _weather_card_spec(),
        )
