import io
import json
import logging
from urllib import error, request

import pytest

from ooh.agent.providers import ModelMessage, ModelProviderError, ModelRequest, OllamaModelProvider


def test_ollama_provider_posts_chat_payload_and_returns_generic_response() -> None:
    captured_requests: list[request.Request] = []
    captured_timeouts: list[float] = []

    def transport(api_request: request.Request, timeout_seconds: float) -> bytes:
        captured_requests.append(api_request)
        captured_timeouts.append(timeout_seconds)
        return json.dumps(
            {
                "model": "qwen3-coder:8b",
                "message": {"role": "assistant", "content": "{\"type\":\"short_answer\"}"},
                "done_reason": "stop",
            }
        ).encode("utf-8")

    provider = OllamaModelProvider(
        base_url="http://ollama.local/",
        timeout_seconds=7,
        transport=transport,
    )

    response = provider.generate(
        ModelRequest(
            model="qwen3-coder:8b",
            messages=[
                ModelMessage(role="system", content="Return JSON."),
                ModelMessage(role="user", content="Generate one question."),
            ],
            response_format="json_object",
            temperature=0,
        )
    )

    assert response.model == "qwen3-coder:8b"
    assert response.content == "{\"type\":\"short_answer\"}"
    assert response.finish_reason == "stop"
    assert captured_timeouts == [7]

    api_request = captured_requests[0]
    assert api_request.full_url == "http://ollama.local/api/chat"
    assert api_request.get_method() == "POST"
    assert api_request.get_header("Content-type") == "application/json"
    payload = json.loads(api_request.data.decode("utf-8"))
    assert payload == {
        "model": "qwen3-coder:8b",
        "messages": [
            {"role": "system", "content": "Return JSON."},
            {"role": "user", "content": "Generate one question."},
        ],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0},
    }


def test_ollama_provider_prefers_response_schema_over_json_mode(caplog) -> None:
    captured_payloads: list[dict] = []
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
        "additionalProperties": False,
    }

    def transport(api_request: request.Request, _timeout_seconds: float) -> bytes:
        captured_payloads.append(json.loads(api_request.data.decode("utf-8")))
        return json.dumps({"message": {"content": "{\"answer\":\"ok\"}"}}).encode("utf-8")

    provider = OllamaModelProvider(base_url="http://ollama.local", transport=transport)

    caplog.set_level(logging.INFO, logger="ooh.agent.providers.ollama")
    response = provider.generate(
        ModelRequest(
            model="qwen3-coder:8b",
            messages=[ModelMessage(role="user", content="Return an answer.")],
            response_format="json_object",
            response_schema=schema,
        )
    )

    assert captured_payloads[0]["format"] == schema
    assert response.raw_response["_ooh"]["output_mode"] == "json_schema"
    assert response.raw_response["_ooh"]["schema_enforced"] is True
    assert response.raw_response["_ooh"]["timeout_seconds"] == 120
    assert "llm call started" in caplog.text
    assert "llm call succeeded" in caplog.text


def test_ollama_provider_fails_when_schema_format_is_unsupported(caplog) -> None:
    captured_payloads: list[dict] = []
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
        "additionalProperties": False,
    }

    def transport(api_request: request.Request, _timeout_seconds: float) -> bytes:
        captured_payloads.append(json.loads(api_request.data.decode("utf-8")))
        raise error.HTTPError(
            api_request.full_url,
            500,
            "Internal Server Error",
            {},
            io.BytesIO(b'{"error":"failed to load model vocabulary required for format"}'),
        )

    provider = OllamaModelProvider(base_url="http://ollama.local", transport=transport)

    caplog.set_level(logging.INFO, logger="ooh.agent.providers.ollama")
    with pytest.raises(ModelProviderError, match="failed to load model vocabulary"):
        provider.generate(
            ModelRequest(
                model="qwen3-coder:8b",
                messages=[ModelMessage(role="user", content="Return an answer.")],
                response_format="json_object",
                response_schema=schema,
            )
        )

    assert captured_payloads[0]["format"] == schema
    assert len(captured_payloads) == 1
    assert "llm call started" in caplog.text
    assert "llm call failed" in caplog.text


def test_ollama_provider_falls_back_to_requested_model() -> None:
    def transport(_api_request: request.Request, _timeout_seconds: float) -> bytes:
        return json.dumps({"message": {"content": "ok"}}).encode("utf-8")

    provider = OllamaModelProvider(base_url="http://ollama.local", transport=transport)

    response = provider.generate(
        ModelRequest(model="fallback-model", messages=[ModelMessage(role="user", content="Hello")])
    )

    assert response.model == "fallback-model"
    assert response.content == "ok"


def test_ollama_provider_wraps_timeout_errors(caplog) -> None:
    def transport(_api_request: request.Request, _timeout_seconds: float) -> bytes:
        raise TimeoutError("timed out")

    provider = OllamaModelProvider(
        base_url="http://ollama.local",
        timeout_seconds=12,
        transport=transport,
    )

    caplog.set_level(logging.INFO, logger="ooh.agent.providers.ollama")
    with pytest.raises(ModelProviderError, match="timed out after 12s"):
        provider.generate(
            ModelRequest(model="qwen3-coder:8b", messages=[ModelMessage(role="user", content="Hello")])
        )

    assert "llm call started" in caplog.text
    assert "llm call failed" in caplog.text


def test_ollama_provider_rejects_invalid_json_response() -> None:
    def transport(_api_request: request.Request, _timeout_seconds: float) -> bytes:
        return b"not-json"

    provider = OllamaModelProvider(base_url="http://ollama.local", transport=transport)

    with pytest.raises(ModelProviderError, match="not valid JSON"):
        provider.generate(
            ModelRequest(model="qwen3-coder:8b", messages=[ModelMessage(role="user", content="Hello")])
        )


def test_ollama_provider_requires_message_content() -> None:
    def transport(_api_request: request.Request, _timeout_seconds: float) -> bytes:
        return json.dumps({"message": {"role": "assistant"}}).encode("utf-8")

    provider = OllamaModelProvider(base_url="http://ollama.local", transport=transport)

    with pytest.raises(ModelProviderError, match="string content"):
        provider.generate(
            ModelRequest(model="qwen3-coder:8b", messages=[ModelMessage(role="user", content="Hello")])
        )
