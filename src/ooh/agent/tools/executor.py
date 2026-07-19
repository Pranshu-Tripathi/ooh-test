from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from ooh.agent.artifacts import AgentArtifactStore
from ooh.agent.tools.common import RepoToolResult, RepositoryToolContext
from ooh.agent.tools.registry import ToolRegistry, default_tool_registry
from ooh.db.models import (
    AgentActivity,
    AgentArtifactType,
    AgentStatus,
    AgentStepType,
    ProvenanceRefType,
)
from ooh.db.repos import AgentArtifactInput, AgentStepInput, AgentTraceRepo, ProvenanceRefInput


@dataclass(frozen=True)
class ToolInvocation:
    tool_name: str
    arguments: dict[str, Any]
    context: RepositoryToolContext
    agent_run_id: UUID | None = None
    sequence: int | None = None
    call_id: str | None = None
    parent_step_id: UUID | None = None
    context_pack_id: UUID | None = None
    iteration: int | None = None


@dataclass(frozen=True)
class ToolExecution:
    invocation: ToolInvocation
    result: RepoToolResult
    duration_ms: int
    agent_step_id: UUID | None = None


@dataclass(frozen=True)
class ToolTraceHandle:
    agent_step_id: UUID | None = None


class ToolTraceRecorder(Protocol):
    def started(self, invocation: ToolInvocation) -> ToolTraceHandle:
        pass

    def succeeded(
        self,
        handle: ToolTraceHandle,
        execution: ToolExecution,
    ) -> None:
        pass

    def failed(
        self,
        handle: ToolTraceHandle,
        invocation: ToolInvocation,
        error: Exception,
        duration_ms: int,
    ) -> None:
        pass


class NoOpToolTraceRecorder:
    def started(self, invocation: ToolInvocation) -> ToolTraceHandle:
        return ToolTraceHandle()

    def succeeded(
        self,
        handle: ToolTraceHandle,
        execution: ToolExecution,
    ) -> None:
        return None

    def failed(
        self,
        handle: ToolTraceHandle,
        invocation: ToolInvocation,
        error: Exception,
        duration_ms: int,
    ) -> None:
        return None


class ToolExecutor:
    def __init__(
        self,
        *,
        registry: ToolRegistry | None = None,
        recorder: ToolTraceRecorder | None = None,
    ) -> None:
        self.registry = registry or default_tool_registry()
        self.recorder = recorder or NoOpToolTraceRecorder()

    def execute(
        self,
        *,
        tool_name: str,
        context: RepositoryToolContext,
        arguments: dict[str, Any],
        agent_run_id: UUID | None = None,
        sequence: int | None = None,
        call_id: str | None = None,
        parent_step_id: UUID | None = None,
        context_pack_id: UUID | None = None,
        iteration: int | None = None,
    ) -> ToolExecution:
        invocation = ToolInvocation(
            tool_name=tool_name,
            arguments=arguments,
            context=context,
            agent_run_id=agent_run_id,
            sequence=sequence,
            call_id=call_id,
            parent_step_id=parent_step_id,
            context_pack_id=context_pack_id,
            iteration=iteration,
        )
        handle = self.recorder.started(invocation)
        started_at = time.monotonic()
        try:
            result = self.registry.get(tool_name).run_json(context, arguments)
        except Exception as exc:
            duration_ms = elapsed_ms(started_at)
            self.recorder.failed(handle, invocation, exc, duration_ms)
            raise

        execution = ToolExecution(
            invocation=invocation,
            result=result,
            duration_ms=elapsed_ms(started_at),
            agent_step_id=handle.agent_step_id,
        )
        self.recorder.succeeded(handle, execution)
        return execution


class AgentTraceToolRecorder:
    def __init__(
        self,
        *,
        agent_trace_repo: AgentTraceRepo,
        artifact_store: AgentArtifactStore,
    ) -> None:
        self.agent_trace_repo = agent_trace_repo
        self.artifact_store = artifact_store

    def started(self, invocation: ToolInvocation) -> ToolTraceHandle:
        if invocation.agent_run_id is None:
            raise ValueError("agent_run_id is required for persisted tool traces")
        if invocation.sequence is None:
            raise ValueError("sequence is required for persisted tool traces")

        step = self.agent_trace_repo.create_step(
            AgentStepInput(
                agent_run_id=invocation.agent_run_id,
                step_type=AgentStepType.TOOL_CALL,
                sequence=invocation.sequence,
                parent_step_id=invocation.parent_step_id,
                context_pack_id=invocation.context_pack_id,
                iteration=invocation.iteration,
                input_summary={
                    "tool_name": invocation.tool_name,
                    "arguments": invocation.arguments,
                    "call_id": invocation.call_id,
                    "snapshot_id": invocation.context.snapshot_id,
                    "commit_sha": invocation.context.commit_sha,
                },
            )
        )
        running_step = self.agent_trace_repo.mark_step_running(
            step.id,
            activity=AgentActivity.EXECUTING_TOOL,
        )
        return ToolTraceHandle(agent_step_id=running_step.id)

    def succeeded(
        self,
        handle: ToolTraceHandle,
        execution: ToolExecution,
    ) -> None:
        if handle.agent_step_id is None or execution.invocation.agent_run_id is None:
            return

        stored_artifact = self.artifact_store.write_json(
            agent_run_id=execution.invocation.agent_run_id,
            file_name=tool_artifact_file_name(execution),
            payload=execution.result.to_dict(),
        )
        artifact = self.agent_trace_repo.create_artifact(
            AgentArtifactInput(
                agent_step_id=handle.agent_step_id,
                artifact_type=AgentArtifactType.TOOL_CALL_RESULT,
                artifact_uri=stored_artifact.artifact_uri,
                content_hash=stored_artifact.content_hash,
            )
        )
        for evidence_ref in execution.result.evidence_refs:
            provenance_ref_input = provenance_input_from_evidence_ref(
                artifact_id=artifact.id,
                evidence_ref=evidence_ref,
            )
            if provenance_ref_input is not None:
                self.agent_trace_repo.create_provenance_ref(provenance_ref_input)

        self.agent_trace_repo.mark_step_finished(
            handle.agent_step_id,
            status=AgentStatus.SUCCEEDED,
            output_summary={
                "tool_name": execution.invocation.tool_name,
                "duration_ms": execution.duration_ms,
                "payload_keys": sorted(execution.result.payload),
                "evidence_ref_count": len(execution.result.evidence_refs),
            },
        )

    def failed(
        self,
        handle: ToolTraceHandle,
        invocation: ToolInvocation,
        error: Exception,
        duration_ms: int,
    ) -> None:
        if handle.agent_step_id is None:
            return

        if invocation.agent_run_id is not None:
            stored_artifact = self.artifact_store.write_json(
                agent_run_id=invocation.agent_run_id,
                file_name=f"tool-{invocation.sequence}-{safe_tool_name(invocation.tool_name)}-error.json",
                payload={
                    "tool_name": invocation.tool_name,
                    "arguments": invocation.arguments,
                    "error_type": type(error).__name__,
                    "error": str(error),
                    "duration_ms": duration_ms,
                },
            )
            self.agent_trace_repo.create_artifact(
                AgentArtifactInput(
                    agent_step_id=handle.agent_step_id,
                    artifact_type=AgentArtifactType.TRACE,
                    artifact_uri=stored_artifact.artifact_uri,
                    content_hash=stored_artifact.content_hash,
                )
            )

        self.agent_trace_repo.mark_step_finished(
            handle.agent_step_id,
            status=AgentStatus.FAILED,
            output_summary={
                "tool_name": invocation.tool_name,
                "duration_ms": duration_ms,
                "error_type": type(error).__name__,
                "error": str(error),
            },
        )


def elapsed_ms(started_at: float) -> int:
    return int((time.monotonic() - started_at) * 1000)


def tool_artifact_file_name(execution: ToolExecution) -> str:
    sequence = (
        execution.invocation.sequence
        if execution.invocation.sequence is not None
        else "unsequenced"
    )
    return f"tool-{sequence}-{safe_tool_name(execution.invocation.tool_name)}-result.json"


def safe_tool_name(tool_name: str) -> str:
    return "".join(character if character.isalnum() else "-" for character in tool_name).strip("-")


def provenance_input_from_evidence_ref(
    *,
    artifact_id: UUID,
    evidence_ref: dict[str, Any],
) -> ProvenanceRefInput | None:
    source_uri = evidence_ref.get("source_uri")
    if not isinstance(source_uri, str) or not source_uri:
        return None

    return ProvenanceRefInput(
        artifact_id=artifact_id,
        ref_type=provenance_type_for_source_type(evidence_ref.get("source_type")),
        ref_uri=source_uri,
        content_hash=evidence_ref.get("content_hash")
        if isinstance(evidence_ref.get("content_hash"), str)
        else None,
        metadata=evidence_ref.get("metadata")
        if isinstance(evidence_ref.get("metadata"), dict)
        else {},
    )


def provenance_type_for_source_type(source_type: object) -> ProvenanceRefType:
    if source_type == "code":
        return ProvenanceRefType.CODE
    if source_type == "guidance":
        return ProvenanceRefType.GUIDANCE
    return ProvenanceRefType.TOOL_CALL
