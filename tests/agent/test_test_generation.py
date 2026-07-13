import json
from typing import Any

import pytest

from ooh.agent.providers import ModelRequest, ModelResponse
from ooh.agent.test_generation import (
    GeneratedTestPayloadError,
    GeneratedTestPipeline,
    build_test_generation_request,
    extract_json_object,
    parse_generated_test_payload,
)


class FakeProvider:
    def __init__(self, content: str) -> None:
        self.content = content
        self.requests: list[ModelRequest] = []

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(model=request.model, content=self.content, raw_response={"ok": True})


def test_build_test_generation_request_asks_for_json() -> None:
    request = build_test_generation_request(
        model="qwen3-coder:8b",
        context_pack={"pack_type": "high_level_design", "source_refs": []},
    )

    assert request.model == "qwen3-coder:8b"
    assert request.response_format == "json_object"
    assert request.metadata["prompt_version"] == "test-generation-v1"
    assert request.messages[0].role == "system"
    assert request.messages[1].role == "user"
    assert "high_level_design" in request.messages[1].content


def test_extract_json_object_from_fenced_output() -> None:
    assert extract_json_object('Here:\n```json\n{"type": "short_answer"}\n```') == (
        '{"type": "short_answer"}'
    )


def test_extract_json_object_ignores_braces_inside_strings() -> None:
    assert extract_json_object('prefix {"question": "What about {x}?", "type": "short_answer"} suffix') == (
        '{"question": "What about {x}?", "type": "short_answer"}'
    )


def test_parse_generated_test_payload_normalizes_valid_payload() -> None:
    payload = parse_generated_test_payload(
        json.dumps(
            {
                "type": "mcq_single",
                "question": "Which file defines the API?",
                "options": [
                    {"id": "A", "text": "README.md"},
                    {"id": "B", "text": "src/ooh/api/main.py"},
                ],
                "correct_option_ids": ["B"],
                "explanation": None,
            }
        )
    )

    assert payload == {
        "schema_version": 1,
        "type": "mcq_single",
        "question": "Which file defines the API?",
        "evidence_refs": [],
        "rubric": [],
        "options": [
            {"id": "A", "text": "README.md"},
            {"id": "B", "text": "src/ooh/api/main.py"},
        ],
        "correct_option_ids": ["B"],
    }


def test_parse_generated_test_payload_rejects_invalid_contract() -> None:
    with pytest.raises(GeneratedTestPayloadError, match="generated test contract"):
        parse_generated_test_payload('{"type": "short_answer", "question": "Missing answer"}')


def test_pipeline_calls_provider_and_returns_candidate() -> None:
    provider = FakeProvider(
        json.dumps(
            {
                "type": "short_answer",
                "question": "What matters?",
                "expected_answer": "The cited evidence matters.",
            }
        )
    )
    pipeline = GeneratedTestPipeline(provider, model="qwen3-coder:8b")

    candidate = pipeline.generate_from_context_pack({"pack_type": "active_pr"})

    assert candidate.payload["type"] == "short_answer"
    assert candidate.prompt_version == "test-generation-v1"
    assert candidate.model_response.raw_response == {"ok": True}
    assert len(provider.requests) == 1


def test_pipeline_accepts_protocol_provider() -> None:
    def _assert_provider_shape(provider: Any) -> None:
        pipeline = GeneratedTestPipeline(provider, model="qwen3-coder:8b")
        assert pipeline.model == "qwen3-coder:8b"

    _assert_provider_shape(FakeProvider("{}"))
