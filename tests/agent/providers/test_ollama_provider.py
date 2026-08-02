import json
from urllib import request

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


def test_ollama_provider_falls_back_to_requested_model() -> None:
    def transport(_api_request: request.Request, _timeout_seconds: float) -> bytes:
        return json.dumps({"message": {"content": "ok"}}).encode("utf-8")

    provider = OllamaModelProvider(base_url="http://ollama.local", transport=transport)

    response = provider.generate(
        ModelRequest(model="fallback-model", messages=[ModelMessage(role="user", content="Hello")])
    )

    assert response.model == "fallback-model"
    assert response.content == "ok"


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
