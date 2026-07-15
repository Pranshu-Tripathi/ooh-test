from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

ModelRole = Literal["system", "user", "assistant"]
ResponseFormat = Literal["text", "json_object"]
JsonSchema = dict[str, Any]


class ModelProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelMessage:
    role: ModelRole
    content: str


@dataclass(frozen=True)
class ModelRequest:
    model: str
    messages: list[ModelMessage]
    response_format: ResponseFormat = "text"
    response_schema: JsonSchema | None = None
    temperature: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelResponse:
    model: str
    content: str
    raw_response: dict[str, Any]
    finish_reason: str | None = None


class ModelProvider(Protocol):
    def generate(self, request: ModelRequest) -> ModelResponse:
        pass
