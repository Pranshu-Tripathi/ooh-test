from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from ooh.agent.contracts import normalize_judge_result_payload
from ooh.agent.providers import ModelMessage, ModelProvider, ModelRequest, ModelResponse
from ooh.agent.test_generation import extract_json_object

ANSWER_JUDGING_PROMPT_VERSION = "answer-judging-v1"


class AnswerJudgingPayloadError(RuntimeError):
    pass


@dataclass(frozen=True)
class AnswerJudgingTurn:
    sequence: int
    action: str
    request: ModelRequest
    model_response: ModelResponse
    validation_error: str | None
    payload: dict[str, Any] | None


@dataclass(frozen=True)
class AnswerJudgingResult:
    payload: dict[str, Any]
    turns: list[AnswerJudgingTurn]
    prompt_version: str


class AnswerJudgingLoop:
    def __init__(self, provider: ModelProvider, *, model: str, max_repair_attempts: int = 1) -> None:
        if max_repair_attempts < 0:
            raise ValueError("max_repair_attempts must be non-negative")
        self.provider = provider
        self.model = model
        self.max_repair_attempts = max_repair_attempts

    def run(
        self,
        *,
        test_payload: dict[str, Any],
        answer_payload: dict[str, Any],
        evidence_refs: list[dict[str, Any]],
    ) -> AnswerJudgingResult:
        turns: list[AnswerJudgingTurn] = []
        request = build_answer_judging_request(
            model=self.model,
            test_payload=test_payload,
            answer_payload=answer_payload,
            evidence_refs=evidence_refs,
        )
        action = "judge"
        repair_attempts = 0
        sequence = 0

        while True:
            sequence += 1
            response = self.provider.generate(request)
            try:
                payload = parse_answer_judging_payload(response.content)
            except AnswerJudgingPayloadError as exc:
                validation_error = str(exc)
                turns.append(
                    AnswerJudgingTurn(
                        sequence=sequence,
                        action=action,
                        request=request,
                        model_response=response,
                        validation_error=validation_error,
                        payload=None,
                    )
                )
                if repair_attempts >= self.max_repair_attempts:
                    raise AnswerJudgingPayloadError(
                        f"judge output did not become valid after {sequence} attempts: {validation_error}"
                    ) from exc
                repair_attempts += 1
                request = build_answer_judging_repair_request(
                    model=self.model,
                    test_payload=test_payload,
                    answer_payload=answer_payload,
                    evidence_refs=evidence_refs,
                    invalid_output=response.content,
                    validation_error=validation_error,
                )
                action = "repair"
                continue

            turns.append(
                AnswerJudgingTurn(
                    sequence=sequence,
                    action=action,
                    request=request,
                    model_response=response,
                    validation_error=None,
                    payload=payload,
                )
            )
            return AnswerJudgingResult(
                payload=payload,
                turns=turns,
                prompt_version=ANSWER_JUDGING_PROMPT_VERSION,
            )


def build_answer_judging_request(
    *,
    model: str,
    test_payload: dict[str, Any],
    answer_payload: dict[str, Any],
    evidence_refs: list[dict[str, Any]],
) -> ModelRequest:
    return ModelRequest(
        model=model,
        response_format="json_object",
        temperature=0,
        messages=[
            ModelMessage(
                role="system",
                content=(
                    "You judge a developer's answer to a repository-understanding test. "
                    "Evaluate only against the generated test, rubric, expected answer, options, "
                    "and evidence refs provided. Return exactly one JSON object and no prose. "
                    "Use status passing, needs_review, or getting_out_of_hand."
                ),
            ),
            ModelMessage(
                role="user",
                content=(
                    "Judge this answer.\n\n"
                    f"Generated test:\n{json.dumps(test_payload, indent=2, sort_keys=True)}\n\n"
                    f"Submitted answer:\n{json.dumps(answer_payload, indent=2, sort_keys=True)}\n\n"
                    f"Evidence refs:\n{json.dumps(evidence_refs, indent=2, sort_keys=True)}"
                ),
            ),
        ],
        metadata={"prompt_version": ANSWER_JUDGING_PROMPT_VERSION},
    )


def build_answer_judging_repair_request(
    *,
    model: str,
    test_payload: dict[str, Any],
    answer_payload: dict[str, Any],
    evidence_refs: list[dict[str, Any]],
    invalid_output: str,
    validation_error: str,
) -> ModelRequest:
    return ModelRequest(
        model=model,
        response_format="json_object",
        temperature=0,
        messages=[
            ModelMessage(
                role="system",
                content=(
                    "You repair answer-judging JSON. Return exactly one corrected JSON object "
                    "and no prose. The JSON must include score, status, feedback, "
                    "missed_concepts, evidence_refs, and optional suggested_learning."
                ),
            ),
            ModelMessage(
                role="user",
                content=(
                    "The previous judge output failed validation. Repair it using the "
                    "validation error and original judging inputs below.\n\n"
                    f"Validation error:\n{validation_error}\n\n"
                    f"Invalid output:\n{invalid_output}\n\n"
                    f"Generated test:\n{json.dumps(test_payload, indent=2, sort_keys=True)}\n\n"
                    f"Submitted answer:\n{json.dumps(answer_payload, indent=2, sort_keys=True)}\n\n"
                    f"Evidence refs:\n{json.dumps(evidence_refs, indent=2, sort_keys=True)}"
                ),
            ),
        ],
        metadata={"prompt_version": ANSWER_JUDGING_PROMPT_VERSION, "repair": True},
    )


def parse_answer_judging_payload(model_content: str) -> dict[str, Any]:
    json_text = extract_json_object(model_content)
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise AnswerJudgingPayloadError("judge output did not contain valid JSON") from exc

    if not isinstance(payload, dict):
        raise AnswerJudgingPayloadError("judge output JSON must be an object")

    try:
        return normalize_judge_result_payload(payload)
    except ValidationError as exc:
        raise AnswerJudgingPayloadError("judge output did not match judge result contract") from exc
