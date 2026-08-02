from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import replace
from time import monotonic
from typing import Any
from urllib import error, request

from ooh.agent.providers.base import (
    ModelProviderError,
    ModelRequest,
    ModelResponse,
    ModelToolCall,
)

Transport = Callable[[request.Request, float], bytes]
logger = logging.getLogger(__name__)


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
        started_at = monotonic()
        endpoint = f"{self.base_url}/api/chat"
        output_mode = self._output_mode(model_request)
        call_action = self._call_action(model_request)
        timeout_seconds = self._request_timeout(model_request)
        prompt_char_count = sum(len(message.content) for message in model_request.messages)
        prompt_byte_count = sum(
            len(message.content.encode("utf-8")) for message in model_request.messages
        )
        logger.info(
            "llm call started provider=ollama model=%s action=%s output_mode=%s "
            "endpoint=%s timeout_seconds=%s prompt_version=%s test_type=%s "
            "message_count=%s prompt_char_count=%s prompt_byte_count=%s "
            "prompt_max_bytes=%s context_original_bytes=%s context_prompt_bytes=%s "
            "context_truncated=%s tool_count=%s",
            model_request.model,
            call_action,
            output_mode,
            endpoint,
            f"{timeout_seconds:g}",
            model_request.metadata.get("prompt_version"),
            model_request.metadata.get("test_type"),
            len(model_request.messages),
            prompt_char_count,
            prompt_byte_count,
            model_request.metadata.get("prompt_max_bytes"),
            model_request.metadata.get("context_original_bytes"),
            model_request.metadata.get("context_prompt_bytes"),
            model_request.metadata.get("context_truncated"),
            len(model_request.tools),
        )
        try:
            response = self._generate_once(model_request, timeout_seconds=timeout_seconds)
        except TimeoutError as exc:
            provider_error = ModelProviderError(
                f"ollama request timed out after {timeout_seconds:g}s"
            )
            self._log_failed_call(
                model_request,
                call_action=call_action,
                output_mode=output_mode,
                started_at=started_at,
                error_value=provider_error,
            )
            raise provider_error from exc
        except error.HTTPError as exc:
            detail = self._http_error_detail(exc)
            provider_error = ModelProviderError(f"ollama request failed: {exc.code} {detail}")
            self._log_failed_call(
                model_request,
                call_action=call_action,
                output_mode=output_mode,
                started_at=started_at,
                error_value=provider_error,
            )
            raise provider_error from exc
        except error.URLError as exc:
            provider_error = ModelProviderError(f"ollama request failed: {exc.reason}")
            self._log_failed_call(
                model_request,
                call_action=call_action,
                output_mode=output_mode,
                started_at=started_at,
                error_value=provider_error,
            )
            raise provider_error from exc
        except ModelProviderError as exc:
            self._log_failed_call(
                model_request,
                call_action=call_action,
                output_mode=output_mode,
                started_at=started_at,
                error_value=exc,
            )
            raise

        duration_ms = round((monotonic() - started_at) * 1000)
        response = self._response_with_provider_metadata(
            response,
            endpoint=endpoint,
            output_mode=output_mode,
            duration_ms=duration_ms,
            timeout_seconds=timeout_seconds,
        )
        logger.info(
            "llm call succeeded provider=ollama model=%s action=%s output_mode=%s "
            "duration_ms=%s finish_reason=%s tool_call_count=%s",
            response.model,
            call_action,
            output_mode,
            duration_ms,
            response.finish_reason,
            len(response.tool_calls),
        )
        return response

    def _generate_once(
        self,
        model_request: ModelRequest,
        *,
        timeout_seconds: float,
    ) -> ModelResponse:
        payload = self._payload(model_request)
        api_request = request.Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )

        response_bytes = self.transport(api_request, timeout_seconds)
        response_payload = self._response_payload(response_bytes)
        message = response_payload.get("message")
        if not isinstance(message, dict):
            raise ModelProviderError("ollama response did not include a message object")

        tool_calls = self._tool_calls(message.get("tool_calls"))
        content = message.get("content")
        if content is None and tool_calls:
            content = ""
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
            tool_calls=tool_calls,
        )

    @staticmethod
    def _payload(model_request: ModelRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model_request.model,
            "messages": [OllamaModelProvider._message_payload(message) for message in model_request.messages],
            "stream": False,
        }
        if model_request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in model_request.tools
            ]
        if model_request.response_schema is not None:
            payload["format"] = model_request.response_schema
        elif model_request.response_format == "json_object":
            payload["format"] = "json"
        if model_request.temperature is not None:
            payload["options"] = {"temperature": model_request.temperature}
        return payload

    @staticmethod
    def _message_payload(message: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.tool_calls:
            payload["tool_calls"] = [
                {
                    **({"id": tool_call.call_id} if tool_call.call_id is not None else {}),
                    "type": "function",
                    "function": {
                        "name": tool_call.name,
                        "arguments": tool_call.arguments,
                    },
                }
                for tool_call in message.tool_calls
            ]
        if message.tool_name is not None:
            payload["tool_name"] = message.tool_name
        return payload

    @staticmethod
    def _tool_calls(value: object) -> list[ModelToolCall]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ModelProviderError("ollama response tool_calls was not a list")

        tool_calls: list[ModelToolCall] = []
        for raw_call in value:
            if not isinstance(raw_call, dict):
                raise ModelProviderError("ollama response tool call was not an object")
            function = raw_call.get("function")
            if not isinstance(function, dict):
                raise ModelProviderError("ollama response tool call did not include a function")
            name = function.get("name")
            if not isinstance(name, str) or not name:
                raise ModelProviderError("ollama response tool call did not include a function name")
            arguments = function.get("arguments", {})
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError as exc:
                    raise ModelProviderError(
                        f"ollama response tool arguments were not valid JSON for {name}"
                    ) from exc
            if not isinstance(arguments, dict):
                raise ModelProviderError(
                    f"ollama response tool arguments were not an object for {name}"
                )
            call_id = raw_call.get("id")
            if not isinstance(call_id, str):
                call_id = None
            tool_calls.append(ModelToolCall(name=name, arguments=arguments, call_id=call_id))
        return tool_calls

    @staticmethod
    def _http_error_detail(exc: error.HTTPError) -> str:
        return exc.read().decode("utf-8", errors="replace")

    @staticmethod
    def _response_with_provider_metadata(
        response: ModelResponse,
        *,
        endpoint: str,
        output_mode: str,
        duration_ms: int,
        timeout_seconds: float,
    ) -> ModelResponse:
        raw_response = dict(response.raw_response)
        provider_metadata = raw_response.get("_ooh")
        if not isinstance(provider_metadata, dict):
            provider_metadata = {}
        raw_response["_ooh"] = {
            **provider_metadata,
            "provider": "ollama",
            "endpoint": endpoint,
            "output_mode": output_mode,
            "schema_enforced": output_mode == "json_schema",
            "duration_ms": duration_ms,
            "timeout_seconds": timeout_seconds,
        }
        return replace(response, raw_response=raw_response)

    @staticmethod
    def _output_mode(model_request: ModelRequest) -> str:
        if model_request.response_schema is not None:
            return "json_schema"
        if model_request.response_format == "json_object":
            return "json"
        if model_request.tools:
            return "tool_calling"
        return "text"

    def _request_timeout(self, model_request: ModelRequest) -> float:
        requested_timeout = model_request.timeout_seconds
        if requested_timeout is None:
            return self.timeout_seconds
        if requested_timeout <= 0:
            raise ModelProviderError("model request timeout_seconds must be positive")
        return min(self.timeout_seconds, requested_timeout)

    @staticmethod
    def _call_action(model_request: ModelRequest) -> str:
        call_action = model_request.metadata.get("call_action")
        if isinstance(call_action, str) and call_action:
            return call_action
        return "generate"

    @staticmethod
    def _log_failed_call(
        model_request: ModelRequest,
        *,
        call_action: str,
        output_mode: str,
        started_at: float,
        error_value: Exception,
    ) -> None:
        logger.error(
            "llm call failed provider=ollama model=%s action=%s output_mode=%s "
            "duration_ms=%s error_type=%s error=%s",
            model_request.model,
            call_action,
            output_mode,
            round((monotonic() - started_at) * 1000),
            type(error_value).__name__,
            str(error_value),
        )

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
