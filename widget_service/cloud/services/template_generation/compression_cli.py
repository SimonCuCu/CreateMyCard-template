"""Read-only CLI for CardPlan Template compression candidate reports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from services.template_generation.engine.cardplan.compression import (
    CompressionConfig,
    TemplateCompressionAnalyzer,
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
    arguments = parser.parse_args()
    report = build_report(max_candidate_pairs=arguments.max_candidate_pairs)
    content = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if arguments.output is None:
        print(content, end="")
        return
    arguments.output.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
