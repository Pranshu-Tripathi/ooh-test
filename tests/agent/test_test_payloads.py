import json

import pytest
from pydantic import ValidationError

from ooh.agent.contracts import (
    generated_test_payload_json_schema,
    generated_test_wire_json_schema,
    normalize_generated_test_payload,
    validate_generated_test_payload,
)


def test_short_answer_payload_normalizes() -> None:
    payload = normalize_generated_test_payload(
        {
            "type": "short_answer",
            "question": "What design rule matters here?",
            "expected_answer": "Use the cited repository guidance.",
            "rubric": [
                {
                    "criterion": "Uses evidence",
                    "description": "References at least one cited source.",
                    "weight": 1,
                }
            ],
        }
    )

    assert payload["schema_version"] == 1
    assert payload["type"] == "short_answer"
    assert payload["expected_answer"] == "Use the cited repository guidance."


def test_generated_test_payload_schema_includes_supported_test_types() -> None:
    schema_text = json.dumps(generated_test_payload_json_schema())

    assert "short_answer" in schema_text
    assert "mcq_single" in schema_text
    assert "mcq_multi" in schema_text


def test_generated_test_payload_schema_can_target_one_test_type() -> None:
    schema_text = json.dumps(generated_test_payload_json_schema("mcq_single"))

    assert "mcq_single" in schema_text
    assert "short_answer" not in schema_text
    assert "mcq_multi" not in schema_text


def test_generated_test_wire_schema_omits_canonical_length_bounds() -> None:
    canonical_schema_text = json.dumps(generated_test_payload_json_schema("mcq_single"))
    wire_schema_text = json.dumps(generated_test_wire_json_schema("mcq_single"))

    assert "maxLength" in canonical_schema_text
    assert "maxLength" not in wire_schema_text
    assert "minLength" not in wire_schema_text
    assert "maxItems" not in wire_schema_text
    assert "minItems" not in wire_schema_text
    assert "evidence_refs" in wire_schema_text


def test_generated_test_wire_schema_can_constrain_visible_evidence_uris() -> None:
    schema = generated_test_wire_json_schema(
        "short_answer",
        evidence_source_uris=["code:src/app.py", "code:src/app.py", "guidance:README.md"],
    )

    assert schema["properties"]["evidence_refs"]["items"]["enum"] == [
        "code:src/app.py",
        "guidance:README.md",
    ]


def test_mcq_single_shorthand_payload_normalizes() -> None:
    payload = normalize_generated_test_payload(
        {
            "type": "mcq_single",
            "question": "Which approach is used?",
            "evidence_refs": ["guidance:README.md"],
            "options": [
                "vector search",
                "LLM-based recommendation",
                "rule-based filtering",
                "keyword matching",
            ],
            "answer": "vector search",
        }
    )

    assert payload["evidence_refs"] == [
        {
            "source_type": "guidance",
            "source_uri": "guidance:README.md",
            "metadata": {},
        }
    ]
    assert payload["options"] == [
        {"id": "A", "text": "vector search"},
        {"id": "B", "text": "LLM-based recommendation"},
        {"id": "C", "text": "rule-based filtering"},
        {"id": "D", "text": "keyword matching"},
    ]
    assert payload["correct_option_ids"] == ["A"]


def test_mcq_single_requires_correct_option_to_exist() -> None:
    with pytest.raises(ValidationError, match="correct option ids are not present"):
        validate_generated_test_payload(
            {
                "type": "mcq_single",
                "question": "Which file defines the API?",
                "options": [
                    {"id": "A", "text": "README.md"},
                    {"id": "B", "text": "src/ooh/api/main.py"},
                ],
                "correct_option_ids": ["C"],
            }
        )


def test_mcq_multi_rejects_duplicate_options() -> None:
    with pytest.raises(ValidationError, match="duplicate option ids"):
        validate_generated_test_payload(
            {
                "type": "mcq_multi",
                "question": "Which files are guidance?",
                "options": [
                    {"id": "A", "text": "AGENTS.md"},
                    {"id": "A", "text": "README.md"},
                ],
                "correct_option_ids": ["A"],
            }
        )
