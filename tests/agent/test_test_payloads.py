import pytest
from pydantic import ValidationError

from ooh.agent.contracts import normalize_generated_test_payload, validate_generated_test_payload


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
