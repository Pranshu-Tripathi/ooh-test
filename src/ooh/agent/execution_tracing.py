from __future__ import annotations

from dataclasses import dataclass, field, replace
from time import monotonic
from typing import Any, Protocol
from uuid import UUID

from ooh.agent.artifacts import AgentArtifactStore
from ooh.agent.providers import ModelProvider, ModelRequest, ModelResponse
from ooh.db.models import (
    AgentActivity,
    AgentArtifactRead,
    AgentArtifactType,
    AgentStatus,
    AgentStepRead,
    AgentStepType,
)
from ooh.db.repos import AgentArtifactInput, AgentStepInput, AgentTraceRepo


@dataclass(frozen=True)
class TraceBranch:
    agent_run_id: UUID
    parent_step_id: UUID
    context_pack_id: UUID | None
    sequence_base: int
    artifact_prefix: str


@dataclass(frozen=True)
class TraceNodeSpec:
    branch: TraceBranch
    step_type: AgentStepType
    sequence: int
    iteration: int | None
    activity: AgentActivity
    action: str
    parent_step_id: UUID | None = None
    input_summary: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TraceHandle:
    step: AgentStepRead
    artifact_prefix: str


@dataclass(frozen=True)
class TracedModelCall:
    response: ModelResponse
    handle: TraceHandle | None = None
    prompt_artifact: AgentArtifactRead | None = None
    response_artifact: AgentArtifactRead | None = None


class ExecutionTracer(Protocol):
    def start(self, spec: TraceNodeSpec) -> TraceHandle:
        pass

    def succeed(
        self,
        handle: TraceHandle,
        *,
        output_summary: dict[str, Any] | None = None,
        warning_summary: list[dict[str, Any]] | None = None,
    ) -> AgentStepRead:
        pass

    def fail(
        self,
        handle: TraceHandle,
        error: Exception,
        *,
        output_summary: dict[str, Any] | None = None,
    ) -> AgentStepRead:
        pass

    def write_json_artifact(
        self,
        handle: TraceHandle,
        *,
        suffix: str,
        artifact_type: AgentArtifactType,
        payload: dict[str, Any],
    ) -> AgentArtifactRead:
        pass


class AgentExecutionTracer:
    def __init__(
        self,
        *,
        agent_trace_repo: AgentTraceRepo,
        artifact_store: AgentArtifactStore,
    ) -> None:
        self.agent_trace_repo = agent_trace_repo
        self.artifact_store = artifact_store

    def start(self, spec: TraceNodeSpec) -> TraceHandle:
        step = self.agent_trace_repo.create_step(
            AgentStepInput(
                agent_run_id=spec.branch.agent_run_id,
                parent_step_id=spec.parent_step_id or spec.branch.parent_step_id,
                context_pack_id=spec.branch.context_pack_id,
                step_type=spec.step_type,
                sequence=spec.sequence,
                iteration=spec.iteration,
                input_summary={"action": spec.action, **spec.input_summary},
            )
        )
        step = self.agent_trace_repo.mark_step_running(step.id, activity=spec.activity)
        return TraceHandle(
            step=step,
            artifact_prefix=(
                f"{spec.branch.artifact_prefix}-{safe_file_part(spec.action)}-{spec.iteration or 0}"
            ),
        )

    def succeed(
        self,
        handle: TraceHandle,
        *,
        output_summary: dict[str, Any] | None = None,
        warning_summary: list[dict[str, Any]] | None = None,
    ) -> AgentStepRead:
        return self.agent_trace_repo.mark_step_finished(
            handle.step.id,
            status=AgentStatus.SUCCEEDED,
            output_summary=output_summary,
            warning_summary=warning_summary,
        )

    def fail(
        self,
        handle: TraceHandle,
        error: Exception,
        *,
        output_summary: dict[str, Any] | None = None,
    ) -> AgentStepRead:
        summary = {
            "error_type": type(error).__name__,
            "error": str(error),
            **(output_summary or {}),
        }
        return self.agent_trace_repo.mark_step_finished(
            handle.step.id,
            status=AgentStatus.FAILED,
            output_summary=summary,
        )

    def write_json_artifact(
        self,
        handle: TraceHandle,
        *,
        suffix: str,
        artifact_type: AgentArtifactType,
        payload: dict[str, Any],
    ) -> AgentArtifactRead:
        stored_artifact = self.artifact_store.write_json(
            agent_run_id=handle.step.agent_run_id,
            file_name=f"{handle.artifact_prefix}-{safe_file_part(suffix)}.json",
            payload=payload,
        )
        return self.agent_trace_repo.create_artifact(
            AgentArtifactInput(
                agent_step_id=handle.step.id,
                artifact_type=artifact_type,
                artifact_uri=stored_artifact.artifact_uri,
                content_hash=stored_artifact.content_hash,
            )
        )


def execute_model_call(
    *,
    provider: ModelProvider,
    request: ModelRequest,
    tracer: ExecutionTracer | None = None,
    spec: TraceNodeSpec | None = None,
) -> TracedModelCall:
    if tracer is None or spec is None:
        return TracedModelCall(response=provider.generate(request))

    spec = replace(
        spec,
        input_summary={
            **spec.input_summary,
            "model": request.model,
            "prompt_version": request.metadata.get("prompt_version"),
            "prompt_bytes": request.metadata.get("prompt_bytes"),
            "message_count": len(request.messages),
            "tool_count": len(request.tools),
            "response_format": request.response_format,
            "schema_enforced": request.response_schema is not None,
        },
    )
    handle = tracer.start(spec)
    started_at = monotonic()
    prompt_artifact: AgentArtifactRead | None = None
    try:
        prompt_artifact = tracer.write_json_artifact(
            handle,
            suffix="prompt",
            artifact_type=AgentArtifactType.PROMPT,
            payload=model_request_payload(request),
        )
        response = provider.generate(request)
        response_artifact = tracer.write_json_artifact(
            handle,
            suffix="raw-response",
            artifact_type=AgentArtifactType.RAW_MODEL_RESPONSE,
            payload=model_response_payload(response),
        )
    except Exception as exc:
        duration_ms = elapsed_ms(started_at)
        try:
            tracer.write_json_artifact(
                handle,
                suffix="error",
                artifact_type=AgentArtifactType.TRACE,
                payload={
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "duration_ms": duration_ms,
                },
            )
        finally:
            tracer.fail(handle, exc, output_summary={"duration_ms": duration_ms})
        raise

    duration_ms = elapsed_ms(started_at)
    tracer.succeed(
        handle,
        output_summary={
            "model": response.model,
            "finish_reason": response.finish_reason,
            "tool_call_count": len(response.tool_calls),
            "response_bytes": len(response.content.encode("utf-8")),
            "duration_ms": duration_ms,
        },
    )
    return TracedModelCall(
        response=response,
        handle=handle,
        prompt_artifact=prompt_artifact,
        response_artifact=response_artifact,
    )


def model_request_payload(request: ModelRequest) -> dict[str, Any]:
    return {
        "model": request.model,
        "messages": [
            {
                "role": message.role,
                "content": message.content,
                "tool_name": message.tool_name,
                "tool_calls": [
                    {
                        "call_id": tool_call.call_id,
                        "name": tool_call.name,
                        "arguments": tool_call.arguments,
                    }
                    for tool_call in message.tool_calls
                ],
            }
            for message in request.messages
        ],
        "response_format": request.response_format,
        "response_schema": request.response_schema,
        "temperature": request.temperature,
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            }
            for tool in request.tools
        ],
        "timeout_seconds": request.timeout_seconds,
        "metadata": request.metadata,
    }


def model_response_payload(response: ModelResponse) -> dict[str, Any]:
    return {
        "model": response.model,
        "content": response.content,
        "finish_reason": response.finish_reason,
        "tool_calls": [
            {
                "call_id": tool_call.call_id,
                "name": tool_call.name,
                "arguments": tool_call.arguments,
            }
            for tool_call in response.tool_calls
        ],
        "raw_response": response.raw_response,
    }


def elapsed_ms(started_at: float) -> int:
    return int((monotonic() - started_at) * 1000)


def safe_file_part(value: str) -> str:
    return "".join(character if character.isalnum() else "-" for character in value).strip("-")
