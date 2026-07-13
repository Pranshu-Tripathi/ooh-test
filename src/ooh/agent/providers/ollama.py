from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from urllib import error, request

from ooh.agent.providers.base import (
    ModelProviderError,
    ModelRequest,
    ModelResponse,
)

Transport = Callable[[request.Request, float], bytes]


class OllamaModelProvider:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float = 120,
        transport: Transport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport or urlopen_bytes

    def generate(self, model_request: ModelRequest) -> ModelResponse:
        payload = self._payload(model_request)
        api_request = request.Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )

        try:
            response_bytes = self.transport(api_request, self.timeout_seconds)
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ModelProviderError(f"ollama request failed: {exc.code} {detail}") from exc
        except error.URLError as exc:
            raise ModelProviderError(f"ollama request failed: {exc.reason}") from exc

        response_payload = self._response_payload(response_bytes)
        message = response_payload.get("message")
        if not isinstance(message, dict):
            raise ModelProviderError("ollama response did not include a message object")

        content = message.get("content")
        if not isinstance(content, str):
            raise ModelProviderError("ollama response message did not include string content")

        response_model = response_payload.get("model")
        if not isinstance(response_model, str):
            response_model = model_request.model

        finish_reason = response_payload.get("done_reason")
        if finish_reason is not None and not isinstance(finish_reason, str):
            finish_reason = None

        return ModelResponse(
            model=response_model,
            content=content,
            raw_response=response_payload,
            finish_reason=finish_reason,
        )

    @staticmethod
    def _payload(model_request: ModelRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model_request.model,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in model_request.messages
            ],
            "stream": False,
        }
        if model_request.response_format == "json_object":
            payload["format"] = "json"
        if model_request.temperature is not None:
            payload["options"] = {"temperature": model_request.temperature}
        return payload

    @staticmethod
    def _response_payload(response_bytes: bytes) -> dict[str, Any]:
        try:
            response_payload = json.loads(response_bytes.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ModelProviderError("ollama response was not valid JSON") from exc

        if not isinstance(response_payload, dict):
            raise ModelProviderError("ollama response was not a JSON object")
        return response_payload


def urlopen_bytes(api_request: request.Request, timeout_seconds: float) -> bytes:
    with request.urlopen(api_request, timeout=timeout_seconds) as response:
        return response.read()
