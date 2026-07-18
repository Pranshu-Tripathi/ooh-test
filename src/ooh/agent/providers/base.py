from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

ModelRole = Literal["system", "user", "assistant", "tool"]
ResponseFormat = Literal["text", "json_object"]
JsonSchema = dict[str, Any]


class ModelProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelToolDefinition:
    name: str
    description: str
    parameters: JsonSchema


@dataclass(frozen=True)
class ModelToolCall:
    name: str
    arguments: dict[str, Any]
    call_id: str | None = None


@dataclass(frozen=True)
class ModelMessage:
    role: ModelRole
    content: str
    tool_calls: list[ModelToolCall] = field(default_factory=list)
    tool_name: str | None = None


@dataclass(frozen=True)
class ModelRequest:
    model: str
    messages: list[ModelMessage]
    response_format: ResponseFormat = "text"
    response_schema: JsonSchema | None = None
    temperature: float | None = None
    tools: list[ModelToolDefinition] = field(default_factory=list)
    timeout_seconds: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelResponse:
    model: str
    content: str
    raw_response: dict[str, Any]
    finish_reason: str | None = None
    tool_calls: list[ModelToolCall] = field(default_factory=list)


class ModelProvider(Protocol):
    def generate(self, request: ModelRequest) -> ModelResponse:
        pass
