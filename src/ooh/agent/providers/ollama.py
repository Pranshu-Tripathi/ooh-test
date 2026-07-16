from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from typing import Any
from urllib import error, request

from ooh.agent.providers.base import (
    ModelProviderError,
    ModelRequest,
    ModelResponse,
)

Transport = Callable[[request.Request, float], bytes]
SCHEMA_FORMAT_UNSUPPORTED_ERRORS = ("failed to load model vocabulary required for format",)


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
        try:
            return self._generate_once(model_request)
        except TimeoutError as exc:
            raise ModelProviderError(
                f"ollama request timed out after {self.timeout_seconds:g}s"
            ) from exc
        except error.HTTPError as exc:
            detail = self._http_error_detail(exc)
            if self._should_retry_without_schema(model_request, detail):
                return self._retry_without_schema(model_request, detail)
            raise ModelProviderError(f"ollama request failed: {exc.code} {detail}") from exc
        except error.URLError as exc:
            raise ModelProviderError(f"ollama request failed: {exc.reason}") from exc

    def _generate_once(self, model_request: ModelRequest) -> ModelResponse:
        payload = self._payload(model_request)
        api_request = request.Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )

        response_bytes = self.transport(api_request, self.timeout_seconds)
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

    def _retry_without_schema(self, model_request: ModelRequest, fallback_reason: str) -> ModelResponse:
        fallback_request = replace(model_request, response_schema=None)
        try:
            response = self._generate_once(fallback_request)
        except TimeoutError as exc:
            raise ModelProviderError(
                f"ollama request timed out after {self.timeout_seconds:g}s"
            ) from exc
        except error.HTTPError as exc:
            detail = self._http_error_detail(exc)
            raise ModelProviderError(f"ollama request failed: {exc.code} {detail}") from exc
        except error.URLError as exc:
            raise ModelProviderError(f"ollama request failed: {exc.reason}") from exc
        return self._response_with_schema_fallback_metadata(response, fallback_reason)

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
        if model_request.response_schema is not None:
            payload["format"] = model_request.response_schema
        elif model_request.response_format == "json_object":
            payload["format"] = "json"
        if model_request.temperature is not None:
            payload["options"] = {"temperature": model_request.temperature}
        return payload

    @staticmethod
    def _should_retry_without_schema(model_request: ModelRequest, detail: str) -> bool:
        return model_request.response_schema is not None and any(
            message in detail for message in SCHEMA_FORMAT_UNSUPPORTED_ERRORS
        )

    @staticmethod
    def _http_error_detail(exc: error.HTTPError) -> str:
        return exc.read().decode("utf-8", errors="replace")

    @staticmethod
    def _response_with_schema_fallback_metadata(
        response: ModelResponse,
        fallback_reason: str,
    ) -> ModelResponse:
        raw_response = dict(response.raw_response)
        provider_metadata = raw_response.get("_ooh")
        if not isinstance(provider_metadata, dict):
            provider_metadata = {}
        raw_response["_ooh"] = {
            **provider_metadata,
            "structured_output_fallback": True,
            "fallback_reason": fallback_reason,
            "fallback_response_format": "json_object",
        }
        return replace(response, raw_response=raw_response)

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
