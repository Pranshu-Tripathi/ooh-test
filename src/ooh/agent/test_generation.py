from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from ooh.agent.contracts import generated_test_payload_json_schema, normalize_generated_test_payload
from ooh.agent.evidence import EvidenceVerificationResult, verify_generated_test_evidence
from ooh.agent.providers import ModelMessage, ModelProvider, ModelRequest, ModelResponse

TEST_GENERATION_PROMPT_VERSION = "test-generation-v1"


class GeneratedTestPayloadError(RuntimeError):
    pass


class GeneratedTestEvidenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class GeneratedTestCandidate:
    payload: dict[str, Any]
    model_response: ModelResponse
    prompt_version: str


@dataclass(frozen=True)
class GeneratedTestLoopTurn:
    sequence: int
    action: str
    request: ModelRequest
    model_response: ModelResponse
    validation_error: str | None
    payload: dict[str, Any] | None
    evidence_error: str | None = None
    evidence_result: EvidenceVerificationResult | None = None


@dataclass(frozen=True)
class GeneratedTestLoopResult:
    payload: dict[str, Any]
    turns: list[GeneratedTestLoopTurn]
    prompt_version: str


class GeneratedTestAgentLoop:
    def __init__(
        self,
        provider: ModelProvider,
        *,
        model: str,
        max_repair_attempts: int = 1,
        max_evidence_regenerations: int = 1,
    ) -> None:
        if max_repair_attempts < 0:
            raise ValueError("max_repair_attempts must be non-negative")
        if max_evidence_regenerations < 0:
            raise ValueError("max_evidence_regenerations must be non-negative")
        self.provider = provider
        self.model = model
        self.max_repair_attempts = max_repair_attempts
        self.max_evidence_regenerations = max_evidence_regenerations

    def run(self, context_pack: dict[str, Any]) -> GeneratedTestLoopResult:
        turns: list[GeneratedTestLoopTurn] = []
        request = build_test_generation_request(model=self.model, context_pack=context_pack)
        action = "generate"
        sequence = 0
        repair_attempts = 0
        evidence_regenerations = 0

        while True:
            sequence += 1
            response = self.provider.generate(request)
            try:
                payload = parse_generated_test_payload(response.content)
            except GeneratedTestPayloadError as exc:
                validation_error = str(exc)
                turns.append(
                    GeneratedTestLoopTurn(
                        sequence=sequence,
                        action=action,
                        request=request,
                        model_response=response,
                        validation_error=validation_error,
                        payload=None,
                    )
                )
                if repair_attempts >= self.max_repair_attempts:
                    raise GeneratedTestPayloadError(
                        f"model output did not become valid after {sequence} attempts: {validation_error}"
                    ) from exc
                repair_attempts += 1
                request = build_test_generation_repair_request(
                    model=self.model,
                    context_pack=context_pack,
                    invalid_output=response.content,
                    validation_error=validation_error,
                )
                action = "repair"
                continue

            evidence_result = verify_generated_test_evidence(payload, context_pack)
            if not evidence_result.is_valid:
                evidence_error = evidence_result.error_message()
                turns.append(
                    GeneratedTestLoopTurn(
                        sequence=sequence,
                        action=action,
                        request=request,
                        model_response=response,
                        validation_error=None,
                        payload=payload,
                        evidence_error=evidence_error,
                        evidence_result=evidence_result,
                    )
                )
                if evidence_regenerations >= self.max_evidence_regenerations:
                    raise GeneratedTestEvidenceError(
                        "model output did not reference valid evidence after "
                        f"{sequence} attempts: {evidence_error}"
                    )
                evidence_regenerations += 1
                request = build_test_generation_evidence_feedback_request(
                    model=self.model,
                    context_pack=context_pack,
                    invalid_payload=payload,
                    evidence_result=evidence_result,
                )
                action = "regenerate_evidence"
                continue

            turns.append(
                GeneratedTestLoopTurn(
                    sequence=sequence,
                    action=action,
                    request=request,
                    model_response=response,
                    validation_error=None,
                    payload=evidence_result.payload,
                    evidence_result=evidence_result,
                )
            )
            return GeneratedTestLoopResult(
                payload=evidence_result.payload,
                turns=turns,
                prompt_version=TEST_GENERATION_PROMPT_VERSION,
            )


class GeneratedTestPipeline:
    def __init__(self, provider: ModelProvider, *, model: str) -> None:
        self.provider = provider
        self.model = model

    def generate_from_context_pack(self, context_pack: dict[str, Any]) -> GeneratedTestCandidate:
        loop = GeneratedTestAgentLoop(
            self.provider,
            model=self.model,
            max_repair_attempts=0,
            max_evidence_regenerations=0,
        )
        result = loop.run(context_pack)
        final_turn = result.turns[-1]
        return GeneratedTestCandidate(
            payload=result.payload,
            model_response=final_turn.model_response,
            prompt_version=result.prompt_version,
        )


def build_test_generation_request(*, model: str, context_pack: dict[str, Any]) -> ModelRequest:
    return ModelRequest(
        model=model,
        response_format="json_object",
        response_schema=generated_test_payload_json_schema(),
        temperature=0.2,
        messages=[
            ModelMessage(
                role="system",
                content=(
                    "You generate repository-understanding tests for developers. "
                    "Return exactly one JSON object and no prose. The JSON must match one of "
                    "these types: short_answer, mcq_single, mcq_multi. Include evidence_refs "
                    "using source_uri values present in the context pack when possible. "
                    "For MCQ options, use objects like {\"id\":\"A\",\"text\":\"...\"}; "
                    "use correct_option_ids, not answer."
                ),
            ),
            ModelMessage(
                role="user",
                content=(
                    "Generate one high-signal test from this context pack. "
                    "Prefer questions that require understanding repository-specific evidence.\n\n"
                    f"{json.dumps(context_pack, indent=2, sort_keys=True)}"
                ),
            ),
        ],
        metadata={"prompt_version": TEST_GENERATION_PROMPT_VERSION},
    )


def build_test_generation_repair_request(
    *,
    model: str,
    context_pack: dict[str, Any],
    invalid_output: str,
    validation_error: str,
) -> ModelRequest:
    return ModelRequest(
        model=model,
        response_format="json_object",
        response_schema=generated_test_payload_json_schema(),
        temperature=0,
        messages=[
            ModelMessage(
                role="system",
                content=(
                    "You repair generated repository-understanding test JSON. "
                    "Return exactly one corrected JSON object and no prose. The JSON must match "
                    "one of these types: short_answer, mcq_single, mcq_multi. "
                    "For MCQ options, use objects like {\"id\":\"A\",\"text\":\"...\"}; "
                    "use correct_option_ids, not answer."
                ),
            ),
            ModelMessage(
                role="user",
                content=(
                    "The previous model output failed validation. Repair it using the "
                    "validation error and context pack below.\n\n"
                    f"Validation error:\n{validation_error}\n\n"
                    f"Invalid output:\n{invalid_output}\n\n"
                    f"Context pack:\n{json.dumps(context_pack, indent=2, sort_keys=True)}"
                ),
            ),
        ],
        metadata={"prompt_version": TEST_GENERATION_PROMPT_VERSION, "repair": True},
    )


def build_test_generation_evidence_feedback_request(
    *,
    model: str,
    context_pack: dict[str, Any],
    invalid_payload: dict[str, Any],
    evidence_result: EvidenceVerificationResult,
) -> ModelRequest:
    return ModelRequest(
        model=model,
        response_format="json_object",
        response_schema=generated_test_payload_json_schema(),
        temperature=0.2,
        messages=[
            ModelMessage(
                role="system",
                content=(
                    "You regenerate repository-understanding test JSON when evidence refs are "
                    "missing or unsupported. Return exactly one JSON object and no prose. Use "
                    "only source_uri values that are present in the context pack source_refs."
                ),
            ),
            ModelMessage(
                role="user",
                content=(
                    "The previous model output passed schema validation but failed evidence "
                    "verification. Regenerate a grounded test. You may keep the same question "
                    "only if it can cite valid evidence refs from the context pack.\n\n"
                    f"Evidence verification:\n{json.dumps(evidence_result.to_dict(), indent=2, sort_keys=True)}\n\n"
                    f"Previous payload:\n{json.dumps(invalid_payload, indent=2, sort_keys=True)}\n\n"
                    f"Context pack:\n{json.dumps(context_pack, indent=2, sort_keys=True)}"
                ),
            ),
        ],
        metadata={"prompt_version": TEST_GENERATION_PROMPT_VERSION, "evidence_feedback": True},
    )


def parse_generated_test_payload(model_content: str) -> dict[str, Any]:
    json_text = extract_json_object(model_content)
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise GeneratedTestPayloadError("model output did not contain valid JSON") from exc

    if not isinstance(payload, dict):
        raise GeneratedTestPayloadError("model output JSON must be an object")

    try:
        return normalize_generated_test_payload(payload)
    except ValidationError as exc:
        raise GeneratedTestPayloadError("model output did not match generated test contract") from exc


def extract_json_object(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        raise GeneratedTestPayloadError("model output was empty")

    fenced = extract_fenced_json(stripped)
    if fenced is not None:
        return fenced

    start_index = stripped.find("{")
    if start_index == -1:
        raise GeneratedTestPayloadError("model output did not contain a JSON object")

    depth = 0
    in_string = False
    escaped = False
    for index, character in enumerate(stripped[start_index:], start=start_index):
        if escaped:
            escaped = False
            continue
        if character == "\\":
            escaped = True
            continue
        if character == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return stripped[start_index : index + 1]

    raise GeneratedTestPayloadError("model output JSON object was not closed")


def extract_fenced_json(text: str) -> str | None:
    fence_start = text.find("```")
    if fence_start == -1:
        return None

    content_start = text.find("\n", fence_start)
    if content_start == -1:
        return None

    fence_end = text.find("```", content_start + 1)
    if fence_end == -1:
        return None

    return text[content_start + 1 : fence_end].strip()
