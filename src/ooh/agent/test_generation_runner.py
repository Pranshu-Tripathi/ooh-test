from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from ooh.agent.artifacts import AgentArtifactStore
from ooh.agent.providers import ModelProvider
from ooh.agent.tool_inspection import (
    FailedToolCall,
    build_inspection_plan,
    context_pack_with_tool_inspection,
    inspection_prompt_payload,
    snapshot_id,
    snapshot_index_uri,
)
from ooh.agent.test_generation import (
    GeneratedTestAgentLoop,
    GeneratedTestEvidenceError,
    GeneratedTestLoopTurn,
    GeneratedTestPayloadError,
)
from ooh.agent.tools import (
    AgentTraceToolRecorder,
    RepoToolError,
    RepositoryToolContext,
    ToolExecution,
    ToolExecutor,
)
from ooh.db.models import (
    AgentArtifactRead,
    AgentArtifactType,
    AgentRunRead,
    AgentRunType,
    AgentStatus,
    AgentStepRead,
    AgentStepType,
    ContextPackSourceType,
    GeneratedTestCategory,
    GeneratedTestRead,
    ProvenanceRefType,
)
from ooh.db.repos import (
    AgentArtifactInput,
    AgentRunInput,
    AgentStepInput,
    AgentTraceRepo,
    ContextPackWithSources,
    GeneratedTestInput,
    GeneratedTestRepo,
    ProvenanceRefInput,
)


@dataclass(frozen=True)
class GeneratedTestRunResult:
    agent_run: AgentRunRead
    generated_tests: list[GeneratedTestRead]


GENERATION_STEP_SEQUENCE = 10_000
PERSIST_STEP_SEQUENCE = 20_000
TOOL_CALL_SEQUENCE_BASE = 100
TOOL_CALL_SEQUENCE_PACK_STRIDE = 1_000


class GeneratedTestRunService:
    def __init__(
        self,
        *,
        provider: ModelProvider,
        model: str,
        artifact_store: AgentArtifactStore,
        agent_trace_repo: AgentTraceRepo,
        generated_test_repo: GeneratedTestRepo,
    ) -> None:
        self.provider = provider
        self.model = model
        self.artifact_store = artifact_store
        self.agent_trace_repo = agent_trace_repo
        self.generated_test_repo = generated_test_repo
        self.agent_loop = GeneratedTestAgentLoop(provider, model=model)
        self.tool_executor = ToolExecutor(
            recorder=AgentTraceToolRecorder(
                agent_trace_repo=agent_trace_repo,
                artifact_store=artifact_store,
            )
        )

    def generate_for_context_packs(
        self,
        context_packs: list[ContextPackWithSources],
        *,
        job_id: UUID | None = None,
    ) -> GeneratedTestRunResult:
        repository_id = self._repository_id(context_packs)
        agent_run = self.agent_trace_repo.create_run(
            AgentRunInput(
                run_type=AgentRunType.TEST_GENERATION,
                job_id=job_id,
                repository_id=repository_id,
                model_profile=self.model,
            )
        )
        agent_run = self.agent_trace_repo.mark_run_running(agent_run.id)

        generation_step: AgentStepRead | None = None
        persist_step: AgentStepRead | None = None
        try:
            generation_step = self._create_generation_step(agent_run.id, context_packs)
            generated_test_inputs: list[GeneratedTestInput] = []
            for context_pack_index, context_pack in enumerate(context_packs):
                generated_test_inputs.append(
                    self._generate_for_context_pack(
                        agent_run.id,
                        generation_step,
                        context_pack,
                        context_pack_index=context_pack_index,
                    )
                )
            generation_step = self.agent_trace_repo.mark_step_finished(
                generation_step.id,
                status=AgentStatus.SUCCEEDED,
                output_summary={"generated_candidate_count": len(generated_test_inputs)},
            )

            persist_step = self._create_persist_step(agent_run.id, generated_test_inputs)
            generated_tests = self.generated_test_repo.create_many(generated_test_inputs)
            persist_step = self.agent_trace_repo.mark_step_finished(
                persist_step.id,
                status=AgentStatus.SUCCEEDED,
                output_summary={"generated_test_count": len(generated_tests)},
            )
        except Exception:
            if generation_step is not None and generation_step.status != AgentStatus.SUCCEEDED:
                self.agent_trace_repo.mark_step_finished(generation_step.id, status=AgentStatus.FAILED)
            if persist_step is not None and persist_step.status != AgentStatus.SUCCEEDED:
                self.agent_trace_repo.mark_step_finished(persist_step.id, status=AgentStatus.FAILED)
            self.agent_trace_repo.mark_run_finished(agent_run.id, status=AgentStatus.FAILED)
            raise

        agent_run = self.agent_trace_repo.mark_run_finished(
            agent_run.id,
            status=AgentStatus.SUCCEEDED,
        )
        return GeneratedTestRunResult(agent_run=agent_run, generated_tests=generated_tests)

    def _generate_for_context_pack(
        self,
        agent_run_id: UUID,
        generation_step: AgentStepRead,
        context_pack: ContextPackWithSources,
        *,
        context_pack_index: int,
    ) -> GeneratedTestInput:
        context_pack_payload = self._read_context_pack(context_pack.context_pack.artifact_uri)
        context_pack_payload = self._inspect_context_pack(
            agent_run_id=agent_run_id,
            context_pack_payload=context_pack_payload,
            context_pack_index=context_pack_index,
        )
        try:
            loop_result = self.agent_loop.run(context_pack_payload)
        except (GeneratedTestPayloadError, GeneratedTestEvidenceError) as exc:
            if exc.turns:
                self._write_turn_artifacts(
                    agent_run_id=agent_run_id,
                    generation_step_id=generation_step.id,
                    context_pack_id=context_pack.context_pack.id,
                    turns=exc.turns,
                )
            raise
        prompt_artifact = self._write_turn_artifacts(
            agent_run_id=agent_run_id,
            generation_step_id=generation_step.id,
            context_pack_id=context_pack.context_pack.id,
            turns=loop_result.turns,
        )
        normalized_payload = loop_result.payload
        validated_artifact = self._write_validated_output_artifact(
            agent_run_id=agent_run_id,
            generation_step_id=generation_step.id,
            context_pack_id=context_pack.context_pack.id,
            payload=normalized_payload,
        )
        self._record_provenance(
            validated_artifact=validated_artifact,
            prompt_artifact=prompt_artifact,
            context_pack=context_pack,
            model=loop_result.turns[-1].model_response.model,
        )

        return GeneratedTestInput(
            repository_id=context_pack.context_pack.repository_id,
            snapshot_id=context_pack.context_pack.snapshot_id,
            drift_event_id=self._drift_event_id(context_pack_payload),
            agent_run_id=agent_run_id,
            context_pack_id=context_pack.context_pack.id,
            category=GeneratedTestCategory(context_pack.context_pack.pack_type.value),
            test_payload=normalized_payload,
            evidence_refs=normalized_payload.get("evidence_refs", []),
            prompt_version=loop_result.prompt_version,
        )

    def _write_turn_artifacts(
        self,
        *,
        agent_run_id: UUID,
        generation_step_id: UUID,
        context_pack_id: UUID,
        turns: list[GeneratedTestLoopTurn],
    ) -> AgentArtifactRead:
        prompt_artifact: AgentArtifactRead | None = None
        for turn in turns:
            prompt_artifact = self._write_prompt_artifact(
                agent_run_id=agent_run_id,
                generation_step_id=generation_step_id,
                context_pack_id=context_pack_id,
                turn=turn,
                request_payload={
                    "model": turn.request.model,
                    "messages": [
                        {"role": message.role, "content": message.content}
                        for message in turn.request.messages
                    ],
                    "response_format": turn.request.response_format,
                    "response_schema": turn.request.response_schema,
                    "temperature": turn.request.temperature,
                    "metadata": turn.request.metadata,
                },
            )
            self._write_raw_response_artifact(
                agent_run_id=agent_run_id,
                generation_step_id=generation_step_id,
                context_pack_id=context_pack_id,
                turn=turn,
                response_payload={
                    "model": turn.model_response.model,
                    "content": turn.model_response.content,
                    "finish_reason": turn.model_response.finish_reason,
                    "raw_response": turn.model_response.raw_response,
                    "validation_error": turn.validation_error,
                    "evidence_error": turn.evidence_error,
                    "evidence_result": (
                        turn.evidence_result.to_dict() if turn.evidence_result is not None else None
                    ),
                },
            )
            if turn.validation_error is not None:
                self._write_validation_error_artifact(
                    agent_run_id=agent_run_id,
                    generation_step_id=generation_step_id,
                    context_pack_id=context_pack_id,
                    turn=turn,
                )
            if turn.evidence_error is not None:
                self._write_evidence_error_artifact(
                    agent_run_id=agent_run_id,
                    generation_step_id=generation_step_id,
                    context_pack_id=context_pack_id,
                    turn=turn,
                )

        if prompt_artifact is None:
            raise ValueError("agent loop produced no turns")
        return prompt_artifact

    def _write_prompt_artifact(
        self,
        *,
        agent_run_id: UUID,
        generation_step_id: UUID,
        context_pack_id: UUID,
        turn: GeneratedTestLoopTurn,
        request_payload: dict[str, Any],
    ) -> AgentArtifactRead:
        stored_artifact = self.artifact_store.write_json(
            agent_run_id=agent_run_id,
            file_name=f"{context_pack_id}-turn-{turn.sequence}-{turn.action}-prompt.json",
            payload=request_payload,
        )
        return self.agent_trace_repo.create_artifact(
            AgentArtifactInput(
                agent_step_id=generation_step_id,
                artifact_type=AgentArtifactType.PROMPT,
                artifact_uri=stored_artifact.artifact_uri,
                content_hash=stored_artifact.content_hash,
            )
        )

    def _write_raw_response_artifact(
        self,
        *,
        agent_run_id: UUID,
        generation_step_id: UUID,
        context_pack_id: UUID,
        turn: GeneratedTestLoopTurn,
        response_payload: dict[str, Any],
    ) -> AgentArtifactRead:
        stored_artifact = self.artifact_store.write_json(
            agent_run_id=agent_run_id,
            file_name=f"{context_pack_id}-turn-{turn.sequence}-{turn.action}-raw-response.json",
            payload=response_payload,
        )
        return self.agent_trace_repo.create_artifact(
            AgentArtifactInput(
                agent_step_id=generation_step_id,
                artifact_type=AgentArtifactType.RAW_MODEL_RESPONSE,
                artifact_uri=stored_artifact.artifact_uri,
                content_hash=stored_artifact.content_hash,
            )
        )

    def _write_validation_error_artifact(
        self,
        *,
        agent_run_id: UUID,
        generation_step_id: UUID,
        context_pack_id: UUID,
        turn: GeneratedTestLoopTurn,
    ) -> AgentArtifactRead:
        stored_artifact = self.artifact_store.write_json(
            agent_run_id=agent_run_id,
            file_name=f"{context_pack_id}-turn-{turn.sequence}-{turn.action}-validation-error.json",
            payload={
                "turn_sequence": turn.sequence,
                "action": turn.action,
                "validation_error": turn.validation_error,
            },
        )
        return self.agent_trace_repo.create_artifact(
            AgentArtifactInput(
                agent_step_id=generation_step_id,
                artifact_type=AgentArtifactType.TRACE,
                artifact_uri=stored_artifact.artifact_uri,
                content_hash=stored_artifact.content_hash,
            )
        )

    def _write_evidence_error_artifact(
        self,
        *,
        agent_run_id: UUID,
        generation_step_id: UUID,
        context_pack_id: UUID,
        turn: GeneratedTestLoopTurn,
    ) -> AgentArtifactRead:
        stored_artifact = self.artifact_store.write_json(
            agent_run_id=agent_run_id,
            file_name=f"{context_pack_id}-turn-{turn.sequence}-{turn.action}-evidence-error.json",
            payload={
                "turn_sequence": turn.sequence,
                "action": turn.action,
                "evidence_error": turn.evidence_error,
                "evidence_result": (
                    turn.evidence_result.to_dict() if turn.evidence_result is not None else None
                ),
            },
        )
        return self.agent_trace_repo.create_artifact(
            AgentArtifactInput(
                agent_step_id=generation_step_id,
                artifact_type=AgentArtifactType.TRACE,
                artifact_uri=stored_artifact.artifact_uri,
                content_hash=stored_artifact.content_hash,
            )
        )

    def _write_validated_output_artifact(
        self,
        *,
        agent_run_id: UUID,
        generation_step_id: UUID,
        context_pack_id: UUID,
        payload: dict[str, Any],
    ) -> AgentArtifactRead:
        stored_artifact = self.artifact_store.write_json(
            agent_run_id=agent_run_id,
            file_name=f"{context_pack_id}-validated-output.json",
            payload=payload,
        )
        return self.agent_trace_repo.create_artifact(
            AgentArtifactInput(
                agent_step_id=generation_step_id,
                artifact_type=AgentArtifactType.VALIDATED_OUTPUT,
                artifact_uri=stored_artifact.artifact_uri,
                content_hash=stored_artifact.content_hash,
            )
        )

    def _record_provenance(
        self,
        *,
        validated_artifact: AgentArtifactRead,
        prompt_artifact: AgentArtifactRead,
        context_pack: ContextPackWithSources,
        model: str,
    ) -> None:
        self.agent_trace_repo.create_provenance_ref(
            ProvenanceRefInput(
                artifact_id=validated_artifact.id,
                ref_type=ProvenanceRefType.CONTEXT_PACK,
                ref_uri=f"context_pack:{context_pack.context_pack.id}",
                content_hash=context_pack.context_pack.content_hash,
                metadata={"pack_type": context_pack.context_pack.pack_type.value},
            )
        )
        self.agent_trace_repo.create_provenance_ref(
            ProvenanceRefInput(
                artifact_id=validated_artifact.id,
                ref_type=ProvenanceRefType.PROMPT,
                ref_uri=f"agent_artifact:{prompt_artifact.id}",
                content_hash=prompt_artifact.content_hash,
            )
        )
        self.agent_trace_repo.create_provenance_ref(
            ProvenanceRefInput(
                artifact_id=validated_artifact.id,
                ref_type=ProvenanceRefType.MODEL,
                ref_uri=f"model:{model}",
            )
        )
        for source in context_pack.sources:
            ref_type = self._provenance_ref_type(source.source_type)
            if ref_type is None:
                continue
            self.agent_trace_repo.create_provenance_ref(
                ProvenanceRefInput(
                    artifact_id=validated_artifact.id,
                    ref_type=ref_type,
                    ref_uri=source.source_uri,
                    content_hash=source.content_hash,
                    metadata={"context_pack_id": str(context_pack.context_pack.id)},
                )
            )

    def _create_generation_step(
        self,
        agent_run_id: UUID,
        context_packs: list[ContextPackWithSources],
    ) -> AgentStepRead:
        step = self.agent_trace_repo.create_step(
            AgentStepInput(
                agent_run_id=agent_run_id,
                step_type=AgentStepType.GENERATE_QUESTIONS,
                sequence=GENERATION_STEP_SEQUENCE,
                input_summary={"context_pack_count": len(context_packs)},
            )
        )
        return self.agent_trace_repo.mark_step_running(step.id)

    def _create_persist_step(
        self,
        agent_run_id: UUID,
        generated_test_inputs: list[GeneratedTestInput],
    ) -> AgentStepRead:
        step = self.agent_trace_repo.create_step(
            AgentStepInput(
                agent_run_id=agent_run_id,
                step_type=AgentStepType.PERSIST_RESULT,
                sequence=PERSIST_STEP_SEQUENCE,
                input_summary={"generated_candidate_count": len(generated_test_inputs)},
            )
        )
        return self.agent_trace_repo.mark_step_running(step.id)

    def _inspect_context_pack(
        self,
        *,
        agent_run_id: UUID,
        context_pack_payload: dict[str, Any],
        context_pack_index: int,
    ) -> dict[str, Any]:
        index_uri = snapshot_index_uri(context_pack_payload)
        if index_uri is None:
            return context_pack_payload

        planned_calls = build_inspection_plan(context_pack_payload)
        if not planned_calls:
            return context_pack_payload

        tool_context = RepositoryToolContext.from_index_uri(
            index_uri,
            snapshot_id=snapshot_id(context_pack_payload),
        )
        executions: list[ToolExecution] = []
        failed_calls: list[FailedToolCall] = []
        sequence_base = TOOL_CALL_SEQUENCE_BASE + (
            context_pack_index * TOOL_CALL_SEQUENCE_PACK_STRIDE
        )

        for call_index, planned_call in enumerate(planned_calls):
            try:
                execution = self.tool_executor.execute(
                    tool_name=planned_call.tool_name,
                    context=tool_context,
                    arguments=planned_call.arguments,
                    agent_run_id=agent_run_id,
                    sequence=sequence_base + call_index,
                    call_id=planned_call.call_id,
                )
            except RepoToolError as exc:
                failed_calls.append(
                    FailedToolCall(
                        call_id=planned_call.call_id,
                        tool_name=planned_call.tool_name,
                        arguments=planned_call.arguments,
                        error_type=type(exc).__name__,
                        error=str(exc),
                    )
                )
                continue
            executions.append(execution)

        return context_pack_with_tool_inspection(
            context_pack_payload,
            inspection_prompt_payload(
                planned_calls=planned_calls,
                executions=executions,
                failed_calls=failed_calls,
            ),
        )

    @staticmethod
    def _read_context_pack(artifact_uri: str) -> dict[str, Any]:
        payload = json.loads(Path(artifact_uri).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"context pack artifact is not an object: {artifact_uri}")
        return payload

    @staticmethod
    def _repository_id(context_packs: list[ContextPackWithSources]) -> UUID:
        if not context_packs:
            raise ValueError("at least one context pack is required")

        repository_id = context_packs[0].context_pack.repository_id
        for context_pack in context_packs:
            if context_pack.context_pack.repository_id != repository_id:
                raise ValueError("all context packs must belong to the same repository")
        return repository_id

    @staticmethod
    def _drift_event_id(context_pack_payload: dict[str, Any]) -> UUID | None:
        drift = context_pack_payload.get("drift")
        if not isinstance(drift, dict):
            return None
        drift_id = drift.get("id")
        if not isinstance(drift_id, str):
            return None
        return UUID(drift_id)

    @staticmethod
    def _provenance_ref_type(source_type: ContextPackSourceType) -> ProvenanceRefType | None:
        if source_type == ContextPackSourceType.CODE:
            return ProvenanceRefType.CODE
        if source_type == ContextPackSourceType.GUIDANCE:
            return ProvenanceRefType.GUIDANCE
        return None
