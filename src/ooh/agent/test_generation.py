from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any

from pydantic import ValidationError

from ooh.agent.contracts import (
    GeneratedTestType,
    generated_test_wire_json_schema,
    normalize_generated_test_payload,
)
from ooh.agent.evidence import EvidenceVerificationResult, verify_generated_test_evidence
from ooh.agent.loop_runtime import LoopDeadline
from ooh.agent.prompt_budget import (
    DEFAULT_GENERATION_PROMPT_MAX_BYTES,
    DEFAULT_TOOL_OBSERVATION_MAX_BYTES,
    PromptContextView,
    build_prompt_context_view,
    serialize_prompt_context,
    truncate_text_bytes,
)
from ooh.agent.providers import ModelMessage, ModelProvider, ModelRequest, ModelResponse

TEST_GENERATION_PROMPT_VERSION = "test-generation-v1"
TEST_TYPE_BY_PACK_TYPE: dict[str, GeneratedTestType] = {
    "high_level_design": "short_answer",
    "low_level_components": "mcq_single",
    "design_decisions": "short_answer",
    "future_improvements": "mcq_multi",
    "active_pr": "short_answer",
}


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


class GeneratedTestPayloadError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        turns: list[GeneratedTestLoopTurn] | None = None,
    ) -> None:
        super().__init__(message)
        self.turns = list(turns or [])


class GeneratedTestEvidenceError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        turns: list[GeneratedTestLoopTurn] | None = None,
    ) -> None:
        super().__init__(message)
        self.turns = list(turns or [])


class GeneratedTestAgentLoop:
    def __init__(
        self,
        provider: ModelProvider,
        *,
        model: str,
        max_repair_attempts: int = 1,
        max_evidence_regenerations: int = 1,
        max_prompt_bytes: int = DEFAULT_GENERATION_PROMPT_MAX_BYTES,
        max_tool_observation_bytes: int = DEFAULT_TOOL_OBSERVATION_MAX_BYTES,
    ) -> None:
        if max_repair_attempts < 0:
            raise ValueError("max_repair_attempts must be non-negative")
        if max_evidence_regenerations < 0:
            raise ValueError("max_evidence_regenerations must be non-negative")
        if max_prompt_bytes < 4_000:
            raise ValueError("max_prompt_bytes must be at least 4000")
        if max_tool_observation_bytes < 500:
            raise ValueError("max_tool_observation_bytes must be at least 500")
        self.provider = provider
        self.model = model
        self.max_repair_attempts = max_repair_attempts
        self.max_evidence_regenerations = max_evidence_regenerations
        self.max_prompt_bytes = max_prompt_bytes
        self.max_tool_observation_bytes = max_tool_observation_bytes

    def run(
        self,
        context_pack: dict[str, Any],
        *,
        deadline: LoopDeadline | None = None,
    ) -> GeneratedTestLoopResult:
        turns: list[GeneratedTestLoopTurn] = []
        test_type = select_generated_test_type(context_pack)
        request = build_test_generation_request(
            model=self.model,
            context_pack=context_pack,
            test_type=test_type,
            max_prompt_bytes=self.max_prompt_bytes,
            max_tool_observation_bytes=self.max_tool_observation_bytes,
        )
        action = "generate"
        sequence = 0
        repair_attempts = 0
        evidence_regenerations = 0

        while True:
            sequence += 1
            if deadline is not None:
                request = replace(
                    request,
                    timeout_seconds=deadline.remaining_seconds(action="calling the test model"),
                )
            response = self.provider.generate(request)
            if deadline is not None:
                deadline.remaining_seconds(action="validating the test model response")
            try:
                payload = parse_generated_test_payload(
                    response.content,
                    expected_type=test_type,
                )
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
                        f"model output did not become valid after {sequence} attempts: {validation_error}",
                        turns=turns,
                    ) from exc
                repair_attempts += 1
                request = build_test_generation_repair_request(
                    model=self.model,
                    context_pack=context_pack,
                    invalid_output=response.content,
                    validation_error=validation_error,
                    test_type=test_type,
                    max_prompt_bytes=self.max_prompt_bytes,
                    max_tool_observation_bytes=self.max_tool_observation_bytes,
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
                        f"{sequence} attempts: {evidence_error}",
                        turns=turns,
                    )
                evidence_regenerations += 1
                request = build_test_generation_evidence_feedback_request(
                    model=self.model,
                    context_pack=context_pack,
                    invalid_payload=payload,
                    evidence_result=evidence_result,
                    test_type=test_type,
                    max_prompt_bytes=self.max_prompt_bytes,
                    max_tool_observation_bytes=self.max_tool_observation_bytes,
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


def select_generated_test_type(context_pack: dict[str, Any]) -> GeneratedTestType:
    pack_type = context_pack.get("pack_type")
    if not isinstance(pack_type, str):
        return "short_answer"
    return TEST_TYPE_BY_PACK_TYPE.get(pack_type, "short_answer")


def build_test_generation_request(
    *,
    model: str,
    context_pack: dict[str, Any],
    test_type: GeneratedTestType | None = None,
    max_prompt_bytes: int = DEFAULT_GENERATION_PROMPT_MAX_BYTES,
    max_tool_observation_bytes: int = DEFAULT_TOOL_OBSERVATION_MAX_BYTES,
) -> ModelRequest:
    requested_test_type = test_type or select_generated_test_type(context_pack)
    tool_inspection_hint = (
        "Use tool_inspection results when present; prefer evidence from tool calls for "
        "symbol-level or line-level questions. "
        if "tool_inspection" in context_pack
        else ""
    )
    system_content = (
        "You generate repository-understanding tests for developers. "
        f"Return exactly one {requested_test_type} JSON object and no prose. "
        "Include evidence_refs as source_uri strings present in the context pack. "
        f"{tool_inspection_hint}"
        f"{test_type_instructions(requested_test_type)}"
    )
    user_prefix = (
        "Generate one high-signal test from this context pack. "
        "Prefer questions that require understanding repository-specific evidence.\n\n"
        "Context pack:\n"
    )
    user_content, context_view = _user_message_with_context(
        context_pack=context_pack,
        system_content=system_content,
        user_prefix=user_prefix,
        max_prompt_bytes=max_prompt_bytes,
        max_tool_observation_bytes=max_tool_observation_bytes,
    )
    metadata = _prompt_metadata(
        context_view,
        system_content=system_content,
        user_content=user_content,
        max_prompt_bytes=max_prompt_bytes,
    )
    return ModelRequest(
        model=model,
        response_format="json_object",
        response_schema=generated_test_wire_json_schema(
            requested_test_type,
            evidence_source_uris=_visible_source_uris(context_view.payload),
        ),
        temperature=0.2,
        messages=[
            ModelMessage(role="system", content=system_content),
            ModelMessage(role="user", content=user_content),
        ],
        metadata={
            **metadata,
            "prompt_version": TEST_GENERATION_PROMPT_VERSION,
            "test_type": requested_test_type,
            "call_action": "generate",
        },
    )


def build_test_generation_repair_request(
    *,
    model: str,
    context_pack: dict[str, Any],
    invalid_output: str,
    validation_error: str,
    test_type: GeneratedTestType | None = None,
    max_prompt_bytes: int = DEFAULT_GENERATION_PROMPT_MAX_BYTES,
    max_tool_observation_bytes: int = DEFAULT_TOOL_OBSERVATION_MAX_BYTES,
) -> ModelRequest:
    requested_test_type = test_type or select_generated_test_type(context_pack)
    system_content = (
        "You repair generated repository-understanding test JSON. "
        f"Return exactly one corrected {requested_test_type} JSON object and no prose. "
        "Return evidence_refs as source_uri strings. "
        f"{test_type_instructions(requested_test_type)}"
    )
    user_prefix = (
        "The previous model output failed validation. Repair it using the validation error "
        "and context pack below.\n\n"
        f"Validation error excerpt:\n{truncate_text_bytes(validation_error, 1_000)}\n\n"
        f"Invalid output excerpt:\n{truncate_text_bytes(invalid_output, 1_500)}\n\n"
        "Context pack:\n"
    )
    user_content, context_view = _user_message_with_context(
        context_pack=context_pack,
        system_content=system_content,
        user_prefix=user_prefix,
        max_prompt_bytes=max_prompt_bytes,
        max_tool_observation_bytes=max_tool_observation_bytes,
    )
    metadata = _prompt_metadata(
        context_view,
        system_content=system_content,
        user_content=user_content,
        max_prompt_bytes=max_prompt_bytes,
    )
    return ModelRequest(
        model=model,
        response_format="json_object",
        response_schema=generated_test_wire_json_schema(
            requested_test_type,
            evidence_source_uris=_visible_source_uris(context_view.payload),
        ),
        temperature=0,
        messages=[
            ModelMessage(role="system", content=system_content),
            ModelMessage(role="user", content=user_content),
        ],
        metadata={
            **metadata,
            "prompt_version": TEST_GENERATION_PROMPT_VERSION,
            "test_type": requested_test_type,
            "call_action": "repair",
            "repair": True,
        },
    )


def build_test_generation_evidence_feedback_request(
    *,
    model: str,
    context_pack: dict[str, Any],
    invalid_payload: dict[str, Any],
    evidence_result: EvidenceVerificationResult,
    test_type: GeneratedTestType | None = None,
    max_prompt_bytes: int = DEFAULT_GENERATION_PROMPT_MAX_BYTES,
    max_tool_observation_bytes: int = DEFAULT_TOOL_OBSERVATION_MAX_BYTES,
) -> ModelRequest:
    requested_test_type = test_type or select_generated_test_type(context_pack)
    system_content = (
        "You regenerate repository-understanding test JSON when evidence refs are missing "
        "or unsupported. Return exactly one JSON object and no prose. Use only source_uri "
        "values present in the context pack and return evidence_refs as source_uri strings."
    )
    evidence_excerpt = truncate_text_bytes(
        json.dumps(evidence_result.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        1_200,
    )
    payload_excerpt = truncate_text_bytes(
        json.dumps(invalid_payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        1_500,
    )
    user_prefix = (
        "The previous model output passed schema validation but failed evidence verification. "
        "Regenerate a grounded test. Keep the question only if it can cite visible evidence.\n\n"
        f"Evidence verification excerpt:\n{evidence_excerpt}\n\n"
        f"Previous payload excerpt:\n{payload_excerpt}\n\n"
        "Context pack:\n"
    )
    user_content, context_view = _user_message_with_context(
        context_pack=context_pack,
        system_content=system_content,
        user_prefix=user_prefix,
        max_prompt_bytes=max_prompt_bytes,
        max_tool_observation_bytes=max_tool_observation_bytes,
    )
    metadata = _prompt_metadata(
        context_view,
        system_content=system_content,
        user_content=user_content,
        max_prompt_bytes=max_prompt_bytes,
    )
    return ModelRequest(
        model=model,
        response_format="json_object",
        response_schema=generated_test_wire_json_schema(
            requested_test_type,
            evidence_source_uris=_visible_source_uris(context_view.payload),
        ),
        temperature=0.2,
        messages=[
            ModelMessage(role="system", content=system_content),
            ModelMessage(role="user", content=user_content),
        ],
        metadata={
            **metadata,
            "prompt_version": TEST_GENERATION_PROMPT_VERSION,
            "test_type": requested_test_type,
            "call_action": "regenerate_evidence",
            "evidence_feedback": True,
        },
    )


def _user_message_with_context(
    *,
    context_pack: dict[str, Any],
    system_content: str,
    user_prefix: str,
    max_prompt_bytes: int,
    max_tool_observation_bytes: int,
) -> tuple[str, PromptContextView]:
    fixed_bytes = len(system_content.encode("utf-8")) + len(user_prefix.encode("utf-8"))
    context_max_bytes = max_prompt_bytes - fixed_bytes
    if context_max_bytes < 1_000:
        raise ValueError("prompt budget leaves fewer than 1000 bytes for repository context")
    context_view = build_prompt_context_view(
        context_pack,
        max_bytes=context_max_bytes,
        tool_observation_max_bytes=max_tool_observation_bytes,
    )
    return f"{user_prefix}{serialize_prompt_context(context_view)}", context_view


def _prompt_metadata(
    context_view: PromptContextView,
    *,
    system_content: str,
    user_content: str,
    max_prompt_bytes: int,
) -> dict[str, Any]:
    prompt_bytes = len(system_content.encode("utf-8")) + len(user_content.encode("utf-8"))
    if prompt_bytes > max_prompt_bytes:
        raise ValueError("generation request exceeded its prompt byte budget")
    return {
        "prompt_bytes": prompt_bytes,
        "prompt_max_bytes": max_prompt_bytes,
        "context_original_bytes": context_view.original_bytes,
        "context_prompt_bytes": context_view.used_bytes,
        "context_prompt_max_bytes": context_view.max_bytes,
        "context_truncated": context_view.truncated,
    }


def _visible_source_uris(prompt_context: dict[str, Any]) -> list[str]:
    source_uris: list[str] = []
    for source_ref in prompt_context.get("source_refs", []):
        if not isinstance(source_ref, dict):
            continue
        source_uri = source_ref.get("source_uri")
        if isinstance(source_uri, str) and source_uri:
            source_uris.append(source_uri)

    tool_inspection = prompt_context.get("tool_inspection")
    if isinstance(tool_inspection, dict):
        for tool_call in tool_inspection.get("tool_calls", []):
            if not isinstance(tool_call, dict):
                continue
            for evidence_ref in tool_call.get("evidence_refs", []):
                if not isinstance(evidence_ref, dict):
                    continue
                source_uri = evidence_ref.get("source_uri")
                if isinstance(source_uri, str) and source_uri:
                    source_uris.append(source_uri)
    return list(dict.fromkeys(source_uris))


def test_type_instructions(test_type: GeneratedTestType) -> str:
    if test_type == "short_answer":
        return "Set type to short_answer and include a non-empty expected_answer."
    if test_type == "mcq_single":
        return (
            "Set type to mcq_single. Include at least two options as objects like "
            "{\"id\":\"A\",\"text\":\"...\"} and exactly one correct_option_ids entry. "
            "Do not use answer or correct_answer."
        )
    return (
        "Set type to mcq_multi. Include at least two options as objects like "
        "{\"id\":\"A\",\"text\":\"...\"} and one or more correct_option_ids entries. "
        "Do not use answer or correct_answer."
    )


def parse_generated_test_payload(
    model_content: str,
    *,
    expected_type: GeneratedTestType | None = None,
) -> dict[str, Any]:
    json_text = extract_json_object(model_content)
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise GeneratedTestPayloadError("model output did not contain valid JSON") from exc

    if not isinstance(payload, dict):
        raise GeneratedTestPayloadError("model output JSON must be an object")

    try:
        return normalize_generated_test_payload(payload, expected_type=expected_type)
    except ValidationError as exc:
        raise GeneratedTestPayloadError(
            f"model output did not match generated test contract: {exc}"
        ) from exc


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
