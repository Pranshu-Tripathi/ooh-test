import json
from typing import Any

import pytest

from ooh.agent.providers import ModelRequest, ModelResponse
from ooh.agent.test_generation import (
    GeneratedTestAgentLoop,
    GeneratedTestEvidenceError,
    GeneratedTestPayloadError,
    GeneratedTestPipeline,
    build_test_generation_evidence_feedback_request,
    build_test_generation_repair_request,
    build_test_generation_request,
    extract_json_object,
    parse_generated_test_payload,
    select_generated_test_type,
)
from ooh.agent.evidence import verify_generated_test_evidence


class FakeProvider:
    def __init__(self, content: str | list[str]) -> None:
        self.contents = [content] if isinstance(content, str) else content
        self.requests: list[ModelRequest] = []

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self.contents) - 1)
        return ModelResponse(model=request.model, content=self.contents[index], raw_response={"ok": True})


def test_build_test_generation_request_asks_for_json() -> None:
    request = build_test_generation_request(
        model="qwen3-coder:8b",
        context_pack={"pack_type": "high_level_design", "source_refs": []},
    )

    assert request.model == "qwen3-coder:8b"
    assert request.response_format == "json_object"
    assert request.response_schema is not None
    assert "short_answer" in json.dumps(request.response_schema)
    assert "mcq_single" not in json.dumps(request.response_schema)
    assert request.metadata["prompt_version"] == "test-generation-v1"
    assert request.metadata["test_type"] == "short_answer"
    assert request.messages[0].role == "system"
    assert request.messages[1].role == "user"
    assert "high_level_design" in request.messages[1].content


def test_build_test_generation_request_constrains_evidence_to_visible_refs() -> None:
    request = build_test_generation_request(
        model="qwen3:8b",
        context_pack={
            "pack_type": "active_pr",
            "source_refs": [
                {"source_type": "code", "source_uri": "code:src/app.py"},
                {"source_type": "guidance", "source_uri": "guidance:README.md"},
            ],
        },
    )

    assert request.response_schema["properties"]["evidence_refs"]["items"]["enum"] == [
        "code:src/app.py",
        "guidance:README.md",
    ]


@pytest.mark.parametrize(
    ("pack_type", "test_type"),
    [
        ("high_level_design", "short_answer"),
        ("low_level_components", "mcq_single"),
        ("design_decisions", "short_answer"),
        ("future_improvements", "mcq_multi"),
        ("active_pr", "short_answer"),
        ("unknown", "short_answer"),
    ],
)
def test_select_generated_test_type_is_deterministic(pack_type: str, test_type: str) -> None:
    assert select_generated_test_type({"pack_type": pack_type}) == test_type


def test_build_test_generation_request_mentions_tool_inspection_when_present() -> None:
    request = build_test_generation_request(
        model="qwen3-coder:8b",
        context_pack={
            "pack_type": "low_level_components",
            "tool_inspection": {
                "tool_calls": [
                    {
                        "tool_name": "repo.read_symbol",
                        "payload": {"symbol": {"qualified_name": "Service.handle"}},
                    }
                ]
            },
        },
    )

    assert "tool_inspection" in request.messages[0].content
    assert "Service.handle" in request.messages[1].content


def test_build_test_generation_request_bounds_large_context_pack() -> None:
    context_pack = {
        "pack_type": "low_level_components",
        "purpose": "Inspect implementation details.",
        "source_refs": [
            {"source_type": "code", "source_uri": f"code:src/module_{index}.py"}
            for index in range(100)
        ],
        "included_files": [
            {
                "path": f"src/module_{index}.py",
                "language": "python",
                "content_excerpt": {"text": "value = 1\n" * 1_000},
            }
            for index in range(20)
        ],
        "tool_inspection": {
            "tool_calls": [
                {
                    "call_id": "read-1",
                    "tool_name": "repo.read_file_range",
                    "payload": {"content": "return value.upper()\n" * 1_000},
                    "evidence_refs": [
                        {"source_type": "code", "source_uri": "code:src/module_0.py"}
                    ],
                }
            ]
        },
    }

    request = build_test_generation_request(
        model="qwen3:8b",
        context_pack=context_pack,
        max_prompt_bytes=4_000,
        max_tool_observation_bytes=800,
    )

    prompt_bytes = sum(len(message.content.encode("utf-8")) for message in request.messages)
    assert prompt_bytes <= 4_000
    assert request.metadata["prompt_bytes"] == prompt_bytes
    assert request.metadata["prompt_max_bytes"] == 4_000
    assert request.metadata["context_original_bytes"] > request.metadata["context_prompt_bytes"]
    assert request.metadata["context_truncated"] is True
    assert "code:src/module_0.py" in request.messages[1].content


def test_build_test_generation_repair_request_includes_error_and_invalid_output() -> None:
    request = build_test_generation_repair_request(
        model="qwen3-coder:8b",
        context_pack={"pack_type": "active_pr"},
        invalid_output='{"type": "short_answer"}',
        validation_error="expected_answer missing",
    )

    assert request.response_format == "json_object"
    assert request.response_schema is not None
    assert request.temperature == 0
    assert request.metadata["repair"] is True
    assert "expected_answer missing" in request.messages[1].content
    assert '{"type": "short_answer"}' in request.messages[1].content


def test_build_test_generation_evidence_feedback_request_includes_rejected_refs() -> None:
    invalid_payload = {
        "type": "short_answer",
        "question": "What changed?",
        "expected_answer": "The missing file changed.",
        "evidence_refs": [{"source_type": "code", "source_uri": "code:missing.py"}],
    }
    context_pack = {"source_refs": [{"source_type": "code", "source_uri": "code:src/app.py"}]}
    evidence_result = verify_generated_test_evidence(invalid_payload, context_pack)

    request = build_test_generation_evidence_feedback_request(
        model="qwen3-coder:8b",
        context_pack=context_pack,
        invalid_payload=invalid_payload,
        evidence_result=evidence_result,
    )

    assert request.response_format == "json_object"
    assert request.response_schema is not None
    assert request.metadata["evidence_feedback"] is True
    assert "code:missing.py" in request.messages[1].content
    assert "code:src/app.py" in request.messages[1].content


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
    with pytest.raises(GeneratedTestPayloadError, match="expected_answer"):
        parse_generated_test_payload('{"type": "short_answer", "question": "Missing answer"}')


def test_parse_generated_test_payload_rejects_a_different_planned_type() -> None:
    with pytest.raises(GeneratedTestPayloadError, match="mcq_single"):
        parse_generated_test_payload(
            json.dumps(
                {
                    "type": "short_answer",
                    "question": "What matters?",
                    "expected_answer": "The evidence matters.",
                }
            ),
            expected_type="mcq_single",
        )


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


def test_agent_loop_repairs_invalid_payload() -> None:
    provider = FakeProvider(
        [
            '{"type": "short_answer", "question": "Missing answer"}',
            json.dumps(
                {
                    "type": "short_answer",
                    "question": "What matters?",
                    "expected_answer": "The cited evidence matters.",
                }
            ),
        ]
    )
    loop = GeneratedTestAgentLoop(provider, model="qwen3-coder:8b")

    result = loop.run({"pack_type": "active_pr"})

    assert result.payload["expected_answer"] == "The cited evidence matters."
    assert [turn.action for turn in result.turns] == ["generate", "repair"]
    assert result.turns[0].validation_error is not None
    assert result.turns[1].validation_error is None
    assert provider.requests[1].metadata["repair"] is True
    assert "expected_answer" in provider.requests[1].messages[1].content


def test_agent_loop_fails_after_repair_budget() -> None:
    provider = FakeProvider('{"type": "short_answer", "question": "Missing answer"}')
    loop = GeneratedTestAgentLoop(provider, model="qwen3-coder:8b", max_repair_attempts=1)

    with pytest.raises(GeneratedTestPayloadError, match="after 2 attempts") as exc_info:
        loop.run({"pack_type": "active_pr"})

    assert len(provider.requests) == 2
    assert [turn.action for turn in exc_info.value.turns] == ["generate", "repair"]
    assert all(turn.validation_error is not None for turn in exc_info.value.turns)


def test_agent_loop_regenerates_invalid_evidence_refs() -> None:
    provider = FakeProvider(
        [
            json.dumps(
                {
                    "type": "short_answer",
                    "question": "What changed?",
                    "expected_answer": "The missing file changed.",
                    "evidence_refs": [{"source_type": "code", "source_uri": "code:missing.py"}],
                }
            ),
            json.dumps(
                {
                    "type": "short_answer",
                    "question": "What changed?",
                    "expected_answer": "src/app.py changed.",
                    "evidence_refs": [{"source_type": "code", "source_uri": "code:src/app.py"}],
                }
            ),
        ]
    )
    loop = GeneratedTestAgentLoop(provider, model="qwen3-coder:8b")

    result = loop.run(
        {
            "pack_type": "active_pr",
            "source_refs": [
                {
                    "source_type": "code",
                    "source_uri": "code:src/app.py",
                    "content_hash": "code-hash",
                }
            ],
        }
    )

    assert result.payload["expected_answer"] == "src/app.py changed."
    assert result.payload["evidence_refs"][0]["content_hash"] == "code-hash"
    assert [turn.action for turn in result.turns] == ["generate", "regenerate_evidence"]
    assert result.turns[0].evidence_error is not None
    assert result.turns[1].evidence_error is None
    assert provider.requests[1].metadata["evidence_feedback"] is True


def test_agent_loop_fails_after_evidence_regeneration_budget() -> None:
    provider = FakeProvider(
        json.dumps(
            {
                "type": "short_answer",
                "question": "What changed?",
                "expected_answer": "The missing file changed.",
                "evidence_refs": [{"source_type": "code", "source_uri": "code:missing.py"}],
            }
        )
    )
    loop = GeneratedTestAgentLoop(provider, model="qwen3-coder:8b", max_evidence_regenerations=1)

    with pytest.raises(GeneratedTestEvidenceError, match="after 2 attempts") as exc_info:
        loop.run(
            {
                "pack_type": "active_pr",
                "source_refs": [{"source_type": "code", "source_uri": "code:src/app.py"}],
            }
        )

    assert len(provider.requests) == 2
    assert [turn.action for turn in exc_info.value.turns] == [
        "generate",
        "regenerate_evidence",
    ]
    assert all(turn.evidence_error is not None for turn in exc_info.value.turns)


def test_pipeline_accepts_protocol_provider() -> None:
    def _assert_provider_shape(provider: Any) -> None:
        pipeline = GeneratedTestPipeline(provider, model="qwen3-coder:8b")
        assert pipeline.model == "qwen3-coder:8b"

    _assert_provider_shape(FakeProvider("{}"))
