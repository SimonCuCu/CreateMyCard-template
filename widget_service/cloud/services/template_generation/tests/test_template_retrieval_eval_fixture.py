import json
from pathlib import Path

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "template_retrieval_eval_cases.jsonl"
_REQUIRED_KEYS = {
    "id",
    "userQuery",
    "size",
    "candidateDataBindings",
    "taskSpecFieldTypesByCapability",
    "expectedThemeId",
    "expectedRequiredOutputFieldsByCapability",
    "expectedMatched",
    "strongDemandCheck",
}


def test_template_retrieval_evaluation_fixture_has_one_hundred_valid_cases() -> None:
    lines = _FIXTURE_PATH.read_text(encoding="utf-8").splitlines()
    cases = [json.loads(line) for line in lines if line.strip()]

    assert len(cases) == 100
    assert {case["id"] for case in cases} == {f"TRE-{index:03d}" for index in range(1, 101)}
    for case in cases:
        assert _REQUIRED_KEYS <= case.keys()
        assert case["userQuery"].strip()
        assert case["candidateDataBindings"]
        assert case["expectedThemeId"].strip()
        assert isinstance(case["expectedMatched"], bool)
        assert case["strongDemandCheck"].strip()
        if case["expectedMatched"]:
            assert isinstance(case.get("expectedTemplateId"), str)
            assert isinstance(case.get("expectedVariantName"), str)
            assert "expectedFailureReason" not in case
        else:
            assert isinstance(case.get("expectedFailureReason"), str)
            assert "expectedTemplateId" not in case
            assert "expectedVariantName" not in case
