from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from ooh.agent.contracts import normalize_generated_test_payload
from ooh.agent.providers import ModelMessage, ModelProvider, ModelRequest, ModelResponse

TEST_GENERATION_PROMPT_VERSION = "test-generation-v1"


class GeneratedTestPayloadError(RuntimeError):
    pass


@dataclass(frozen=True)
class GeneratedTestCandidate:
    payload: dict[str, Any]
    model_response: ModelResponse
    prompt_version: str


class GeneratedTestPipeline:
    def __init__(self, provider: ModelProvider, *, model: str) -> None:
        self.provider = provider
        self.model = model

    def generate_from_context_pack(self, context_pack: dict[str, Any]) -> GeneratedTestCandidate:
        request = build_test_generation_request(
            model=self.model,
            context_pack=context_pack,
        )
        response = self.provider.generate(request)
        payload = parse_generated_test_payload(response.content)
        return GeneratedTestCandidate(
            payload=payload,
            model_response=response,
            prompt_version=TEST_GENERATION_PROMPT_VERSION,
        )


def build_test_generation_request(*, model: str, context_pack: dict[str, Any]) -> ModelRequest:
    return ModelRequest(
        model=model,
        response_format="json_object",
        temperature=0.2,
        messages=[
            ModelMessage(
                role="system",
                content=(
                    "You generate repository-understanding tests for developers. "
                    "Return exactly one JSON object and no prose. The JSON must match one of "
                    "these types: short_answer, mcq_single, mcq_multi. Include evidence_refs "
                    "using source_uri values present in the context pack when possible."
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
