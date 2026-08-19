#!/usr/bin/env python3
"""Run the first-layer template retrieval evaluator against an OpenAI-compatible API.

The API key is read only from ``DEEPSEEK_API_KEY``.  This script deliberately
does not load or write a .env file, and its JSONL output never includes the key.
"""

from __future__ import annotations

# ruff: noqa: E402
import argparse
import asyncio
import json
import os
import ssl
import sys
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

import certifi

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "cloud"))

from models.generation import CandidateDataBinding, TaskSpec  # noqa: E402
from services.template_generation.engine.advanced.data_shape import extract_data_shape  # noqa: E402
from services.template_generation.engine.advanced.models import TemplateRetrievalQuery  # noqa: E402
from services.template_generation.engine.advanced.scope_planner import (  # noqa: E402
    build_template_retrieval_prompt,
)
from services.template_generation.engine.cardplan.registry import (
    get_cardplan_registry,  # noqa: E402
)
from services.template_generation.engine.cardplan.template_retrieval import (  # noqa: E402
    TemplateRetrievalMiss,
    retrieve_template_variant,
)
from services.template_generation.model_client import _parse_json_object  # noqa: E402


def _set_leaf(target: dict[str, Any], pointer: str, value: dict[str, Any]) -> None:
    current: Any = target
    parts = [part for part in pointer.removeprefix("/").split("/") if part]
    for index, part in enumerate(parts):
        is_last = index == len(parts) - 1
        next_is_list = not is_last and parts[index + 1].isdigit()
        if isinstance(current, list):
            while len(current) <= int(part):
                current.append({})
            if is_last:
                current[int(part)] = value
            else:
                if not isinstance(current[int(part)], (dict, list)):
                    current[int(part)] = [] if next_is_list else {}
                current = current[int(part)]
        else:
            if is_last:
                current[part] = value
            else:
                if part not in current:
                    current[part] = [] if next_is_list else {}
                current = current[part]


def _case_inputs(
    case: dict[str, Any],
) -> tuple[TaskSpec, tuple[CandidateDataBinding, ...], dict[str, Any]]:
    schema: dict[str, Any] = {}
    bindings = tuple(
        CandidateDataBinding.model_validate(item) for item in case["candidateDataBindings"]
    )
    type_map = case["taskSpecFieldTypesByCapability"]
    for binding in bindings:
        for path, data_type in type_map.get(binding.capabilityId, {}).items():
            _set_leaf(
                schema,
                f"{binding.writeResultTo.rstrip('/')}{path}",
                {"type": data_type, "description": "evaluation fixture field"},
            )
    task_spec = TaskSpec(userQuery=case["userQuery"], size=case["size"], dataModelSchema=schema)
    card_spec = {
        "suggestSize": case["size"],
        "dataBindings": [
            item.model_dump(exclude={"candidateOutputFields", "arguments"}) for item in bindings
        ],
    }
    return task_spec, bindings, card_spec


def _call_api(
    messages: list[dict[str, str]],
    api_key: str,
    base_url: str,
    model: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    payload = json.dumps(
        {
            "model": model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": 2048,
            "response_format": {"type": "json_object"},
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(request, timeout=90, context=ssl_context) as response:
        body = json.load(response)
    return _parse_json_object(body["choices"][0]["message"]["content"]), body.get("usage")


def _field_sets(value: dict[str, tuple[str, ...]]) -> set[tuple[str, str]]:
    return {(capability, path) for capability, paths in value.items() for path in paths}


async def _evaluate_case(
    case: dict[str, Any],
    *,
    api_key: str,
    base_url: str,
    model: str,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    task_spec, bindings, card_spec = _case_inputs(case)
    registry = get_cardplan_registry()
    prompt = build_template_retrieval_prompt(
        task_spec,
        extract_data_shape(task_spec),
        registry,
        {item.capabilityId: tuple(item.candidateOutputFields) for item in bindings},
    )
    result: dict[str, Any] = {"id": case["id"], "expectedMatched": case["expectedMatched"]}
    try:
        async with semaphore:
            raw, usage = await asyncio.to_thread(_call_api, prompt, api_key, base_url, model)
        query = TemplateRetrievalQuery.model_validate(raw)
        result.update({"llmValid": True, "query": query.model_dump(by_alias=True), "usage": usage})
    except Exception as exc:  # output is an evaluation observation, not a production error path
        result.update({"llmValid": False, "error": f"{type(exc).__name__}: {exc}"})
        return result

    try:
        match = retrieve_template_variant(query, task_spec, registry, bindings, card_spec)
        result.update(
            {
                "matched": True,
                "templateId": match.template_id,
                "variantName": match.variant_name,
            }
        )
    except (TemplateRetrievalMiss, ValueError) as exc:
        result.update({"matched": False, "retrievalError": str(exc)})
    return result


def _summary(cases: list[dict[str, Any]], results: list[dict[str, Any]]) -> dict[str, Any]:
    expected_by_id = {case["id"]: case for case in cases}
    metrics: Counter[str] = Counter()
    expected_fields = predicted_fields = overlap_fields = 0
    hard_omission_cases: list[str] = []
    for result in results:
        case = expected_by_id[result["id"]]
        metrics["total"] += 1
        if not result.get("llmValid"):
            metrics["llm_invalid"] += 1
            continue
        metrics["llm_valid"] += 1
        query = TemplateRetrievalQuery.model_validate(result["query"])
        expected = _field_sets(case["expectedRequiredOutputFieldsByCapability"])
        predicted = _field_sets(query.required_output_fields_by_capability)
        expected_fields += len(expected)
        predicted_fields += len(predicted)
        overlap = len(expected & predicted)
        overlap_fields += overlap
        if expected - predicted:
            hard_omission_cases.append(result["id"])
        if query.theme_id == case["expectedThemeId"]:
            metrics["theme_exact"] += 1
        if predicted == expected:
            metrics["field_set_exact"] += 1
        if result["matched"] == case["expectedMatched"]:
            metrics["route_decision_correct"] += 1
        if case["expectedMatched"]:
            metrics["positive_total"] += 1
        else:
            metrics["negative_total"] += 1
        if case["expectedMatched"] and result["matched"]:
            if (
                result.get("templateId") == case["expectedTemplateId"]
                and result.get("variantName") == case["expectedVariantName"]
            ):
                metrics["positive_template_exact"] += 1
        if not case["expectedMatched"] and not result["matched"]:
            metrics["negative_safe_reject"] += 1
    valid = metrics["llm_valid"]
    return {
        **dict(metrics),
        "llm_valid_rate": metrics["llm_valid"] / metrics["total"],
        "theme_exact_rate": metrics["theme_exact"] / valid if valid else 0,
        "field_set_exact_rate": metrics["field_set_exact"] / valid if valid else 0,
        "strong_demand_field_recall": overlap_fields / expected_fields if expected_fields else 0,
        "strong_demand_field_precision": (
            overlap_fields / predicted_fields if predicted_fields else 0
        ),
        "strong_demand_omission_case_count": len(hard_omission_cases),
        "strong_demand_omission_case_ids": hard_omission_cases,
        "route_decision_accuracy": metrics["route_decision_correct"] / metrics["total"],
        "positive_template_exact_rate": (
            metrics["positive_template_exact"] / metrics["positive_total"]
            if metrics["positive_total"]
            else 0
        ),
        "negative_safe_reject_rate": (
            metrics["negative_safe_reject"] / metrics["negative_total"]
            if metrics["negative_total"]
            else 0
        ),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fixture",
        type=Path,
        default=PROJECT_ROOT / "tests/fixtures/template_retrieval_eval_cases.jsonl",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--base-url", default=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    )
    parser.add_argument("--model", default=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"))
    args = parser.parse_args()
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise SystemExit("DEEPSEEK_API_KEY is required")
    cases = [
        json.loads(line)
        for line in args.fixture.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if args.limit is not None:
        cases = cases[: args.limit]
    semaphore = asyncio.Semaphore(args.concurrency)
    evaluations = (
        _evaluate_case(
            case,
            api_key=api_key,
            base_url=args.base_url,
            model=args.model,
            semaphore=semaphore,
        )
        for case in cases
    )
    results = await asyncio.gather(*evaluations)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    output = "\n".join(json.dumps(item, ensure_ascii=False) for item in results) + "\n"
    args.output.write_text(output, encoding="utf-8")
    print(json.dumps(_summary(cases, results), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
