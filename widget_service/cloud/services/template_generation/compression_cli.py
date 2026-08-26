"""Read-only CLI for CardPlan Template compression candidate reports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from services.template_generation.engine.cardplan.compression import (
    CompressionConfig,
    TemplateCompressionAnalyzer,
    write_compressed_component_dag,
)
from services.template_generation.engine.cardplan.provider_bundle import (
    load_provider_bundles,
)
from services.template_generation.engine.cardplan.registry import get_cardplan_registry


def build_report(*, max_candidate_pairs: int) -> dict[str, Any]:
    registry = get_cardplan_registry()
    definitions = tuple(
        registry.require_template(template_id)
        for template_id in registry.provider_template_ids
    )
    analyzer = TemplateCompressionAnalyzer(
        CompressionConfig(max_candidate_pairs=max_candidate_pairs)
    )
    return analyzer.analyze(definitions).to_json_dict()


def provider_definitions() -> tuple:
    registry = get_cardplan_registry()
    return tuple(
        definition
        for bundle in load_provider_bundles(registry.source_root / "providers")
        for definition in bundle.templates
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze Provider Templates without modifying source assets."
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional JSON output path. Standard output is used when omitted.",
    )
    parser.add_argument("--max-candidate-pairs", type=int, default=20_000)
    parser.add_argument(
        "--write-compressed-dag",
        type=Path,
        help="Write the shared Provider component DAG for direct inspection.",
    )
    arguments = parser.parse_args()
    definitions = provider_definitions()
    analyzer = TemplateCompressionAnalyzer(
        CompressionConfig(max_candidate_pairs=arguments.max_candidate_pairs)
    )
    report = analyzer.analyze(definitions).to_json_dict()
    if arguments.write_compressed_dag is not None:
        write_compressed_component_dag(definitions, arguments.write_compressed_dag)
    content = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if arguments.output is None:
        print(content, end="")
        return
    arguments.output.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
