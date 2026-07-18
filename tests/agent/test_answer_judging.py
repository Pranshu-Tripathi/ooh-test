import json

import pytest

from ooh.agent.answer_judging import (
    AnswerJudgingLoop,
    AnswerJudgingPayloadError,
    build_answer_judging_repair_request,
    build_answer_judging_request,
    parse_answer_judging_payload,
)
from ooh.agent.providers import ModelRequest, ModelResponse


class FakeProvider:
    def __init__(self, content: str | list[str]) -> None:
        self.contents = [content] if isinstance(content, str) else content
        self.requests: list[ModelRequest] = []

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self.contents) - 1)
        return ModelResponse(model=request.model, content=self.contents[index], raw_response={"ok": True})


def test_build_answer_judging_request_asks_for_json() -> None:
    request = build_answer_judging_request(
        model="deepseek-r1:8b",
        test_payload={"type": "short_answer", "question": "What matters?"},
        answer_payload={"answer_text": "Evidence matters."},
        evidence_refs=[{"source_type": "code", "source_uri": "code:src/app.py"}],
    )

    assert request.model == "deepseek-r1:8b"
    assert request.response_format == "json_object"
    assert request.response_schema is not None
    assert "needs_review" in json.dumps(request.response_schema)
    assert "maxLength" not in json.dumps(request.response_schema)
    assert request.response_schema["properties"]["evidence_refs"]["items"]["enum"] == [
        "code:src/app.py"
    ]
    assert request.metadata["prompt_version"] == "answer-judging-v1"
    assert "What matters?" in request.messages[1].content
    assert "Evidence matters." in request.messages[1].content


def test_build_answer_judging_repair_request_includes_error_and_invalid_output() -> None:
    request = build_answer_judging_repair_request(
        model="deepseek-r1:8b",
        test_payload={"type": "short_answer"},
        answer_payload={"answer_text": "ok"},
        evidence_refs=[],
        invalid_output='{"score": 2}',
        validation_error="score must be less than or equal to 1",
    )

    assert request.response_format == "json_object"
    assert request.response_schema is not None
    assert request.metadata["repair"] is True
    assert '{"score": 2}' in request.messages[1].content
    assert "score must be less than or equal to 1" in request.messages[1].content


def test_parse_answer_judging_payload_normalizes_status() -> None:
    payload = parse_answer_judging_payload(
        json.dumps(
            {
                "score": 0.8,
                "status": "PASSING",
                "feedback": "Good answer.",
                "missed_concepts": ["  "],
            }
        )
    )

    assert payload == {
        "score": 0.8,
        "status": "passing",
        "missed_concepts": [],
        "feedback": "Good answer.",
        "evidence_refs": [],
    }


def test_parse_answer_judging_payload_normalizes_wire_evidence_uris() -> None:
    payload = parse_answer_judging_payload(
        json.dumps(
            {
                "score": 1,
                "status": "passing",
                "feedback": "Grounded answer.",
                "missed_concepts": [],
                "evidence_refs": ["code:src/app.py"],
            }
        )
    )

    assert payload["evidence_refs"] == [
        {
            "source_type": "code",
            "source_uri": "code:src/app.py",
            "metadata": {},
        }
    ]


def test_parse_answer_judging_payload_rejects_invalid_contract() -> None:
    with pytest.raises(AnswerJudgingPayloadError, match="judge result contract"):
        parse_answer_judging_payload('{"score": 2, "status": "passing", "feedback": "too high"}')


def test_answer_judging_loop_repairs_invalid_payload() -> None:
    provider = FakeProvider(
        [
            '{"score": 2, "status": "passing", "feedback": "too high"}',
            json.dumps(
                {
                    "score": 0.7,
                    "status": "needs_review",
                    "feedback": "Partially correct.",
                    "missed_concepts": ["worker boundaries"],
                }
            ),
        ]
    )
    loop = AnswerJudgingLoop(provider, model="deepseek-r1:8b")

    result = loop.run(
        test_payload={"type": "short_answer", "question": "What matters?"},
        answer_payload={"answer_text": "Some of it."},
        evidence_refs=[],
    )

    assert result.payload["score"] == 0.7
    assert result.payload["status"] == "needs_review"
    assert [turn.action for turn in result.turns] == ["judge", "repair"]
    assert provider.requests[1].metadata["repair"] is True


def test_answer_judging_loop_fails_after_repair_budget() -> None:
    provider = FakeProvider('{"score": 2, "status": "passing", "feedback": "too high"}')
    loop = AnswerJudgingLoop(provider, model="deepseek-r1:8b", max_repair_attempts=1)

    with pytest.raises(AnswerJudgingPayloadError, match="after 2 attempts"):
        loop.run(
            test_payload={"type": "short_answer", "question": "What matters?"},
            answer_payload={"answer_text": "Some of it."},
            evidence_refs=[],
        )

    assert len(provider.requests) == 2
