import json
from datetime import UTC, datetime
from uuid import uuid4

from ooh.api.schemas.repositories import GeneratedTestResponse
from ooh.db.models import GeneratedTestCategory, GeneratedTestRead


def test_generated_test_api_response_never_serializes_the_answer_key() -> None:
    generated_test = GeneratedTestRead(
        id=uuid4(),
        repository_id=uuid4(),
        snapshot_id=uuid4(),
        drift_event_id=None,
        agent_run_id=uuid4(),
        context_pack_id=uuid4(),
        category=GeneratedTestCategory.DESIGN_DECISIONS,
        test_payload={
            "type": "mcq_single",
            "question": "Which payload reaches the browser?",
            "options": [
                {"id": "A", "text": "The public presentation payload"},
                {"id": "B", "text": "The canonical grading payload"},
            ],
            "correct_option_ids": ["A"],
            "rubric": [
                {
                    "criterion": "Correct boundary",
                    "description": "Selects the public presentation payload.",
                    "weight": 1,
                }
            ],
            "explanation": "The API sanitizes the canonical record.",
            "evidence_refs": [
                {
                    "source_type": "code",
                    "source_uri": "code:src/ooh/api/schemas/repositories.py",
                    "metadata": {"answer_hint": "A"},
                }
            ],
        },
        evidence_refs=[],
        prompt_version="test-generation-v1",
        created_at=datetime.now(UTC),
    )

    response_json = json.dumps(
        GeneratedTestResponse.from_record(generated_test).model_dump(mode="json")
    )

    assert "presentation_payload" in response_json
    assert "correct_option_ids" not in response_json
    assert "expected_answer" not in response_json
    assert "rubric" not in response_json
    assert "explanation" not in response_json
    assert "answer_hint" not in response_json
