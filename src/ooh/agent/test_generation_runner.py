from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from ooh.agent.artifacts import AgentArtifactStore
from ooh.agent.execution_tracing import AgentExecutionTracer, TraceBranch
from ooh.agent.loop_runtime import DEFAULT_AGENT_LOOP_TIMEOUT_SECONDS, LoopDeadline
from ooh.agent.providers import ModelProvider
from ooh.agent.prompt_budget import (
    DEFAULT_GENERATION_PROMPT_MAX_BYTES,
    DEFAULT_TOOL_OBSERVATION_MAX_BYTES,
)
from ooh.agent.repository_inspection import (
    INSPECTION_PROMPT_VERSION,
    ModelDirectedRepositoryInspector,
    RepositoryInspectionResult,
)
from ooh.agent.tool_inspection import (
    context_pack_with_tool_inspection,
    snapshot_id,
    snapshot_index_uri,
)
from ooh.agent.test_generation import (
    GeneratedTestAgentLoop,
)
from ooh.agent.tools import (
    AgentTraceToolRecorder,
    RepositoryToolContext,
    ToolExecutor,
)
from ooh.db.models import (
    AgentActivity,
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

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GeneratedTestRunResult:
    agent_run: AgentRunRead
    generated_tests: list[GeneratedTestRead]


PLAN_STEP_SEQUENCE_BASE = 10
TOOL_CALL_SEQUENCE_BASE = 1_000
CONTEXT_PACK_SEQUENCE_STRIDE = 1_000_000
GENERATION_STEP_SEQUENCE = 1
PERSIST_STEP_SEQUENCE = 20_000_000


class GeneratedTestRunService:
    def __init__(
        self,
        *,
        provider: ModelProvider,
        model: str,
        artifact_store: AgentArtifactStore,
        agent_trace_repo: AgentTraceRepo,
        generated_test_repo: GeneratedTestRepo,
        max_prompt_bytes: int = DEFAULT_GENERATION_PROMPT_MAX_BYTES,
        max_tool_observation_bytes: int = DEFAULT_TOOL_OBSERVATION_MAX_BYTES,
        max_loop_duration_seconds: float = DEFAULT_AGENT_LOOP_TIMEOUT_SECONDS,
    ) -> None:
        self.provider = provider
        self.model = model
        self.artifact_store = artifact_store
        self.agent_trace_repo = agent_trace_repo
        self.generated_test_repo = generated_test_repo
        self.max_prompt_bytes = max_prompt_bytes
        self.max_tool_observation_bytes = max_tool_observation_bytes
        self.max_loop_duration_seconds = max_loop_duration_seconds
        self.tracer = AgentExecutionTracer(
            agent_trace_repo=agent_trace_repo,
            artifact_store=artifact_store,
        )
        self.agent_loop = GeneratedTestAgentLoop(
            provider,
            model=model,
            max_prompt_bytes=max_prompt_bytes,
            max_tool_observation_bytes=max_tool_observation_bytes,
            tracer=self.tracer,
        )
        self.tool_executor = ToolExecutor(
            recorder=AgentTraceToolRecorder(
                agent_trace_repo=agent_trace_repo,
                artifact_store=artifact_store,
            )
        )
        self.repository_inspector = ModelDirectedRepositoryInspector(
            provider=provider,
            model=model,
            tool_executor=self.tool_executor,
            max_prompt_bytes=max_prompt_bytes,
            max_tool_observation_bytes=max_tool_observation_bytes,
            tracer=self.tracer,
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
        logger.info(
            "test generation run started agent_run_id=%s job_id=%s repository_id=%s "
            "context_pack_count=%s model=%s",
            agent_run.id,
            job_id,
            repository_id,
            len(context_packs),
            self.model,
        )

        generation_step: AgentStepRead | None = None
        persist_step: AgentStepRead | None = None
        try:
            generation_step = self._create_generation_step(agent_run.id, context_packs)
            generated_test_inputs: list[GeneratedTestInput] = []
            for context_pack_index, context_pack in enumerate(context_packs):
                logger.info(
                    "test generation context pack started agent_run_id=%s context_pack_id=%s "
                    "pack_type=%s position=%s/%s",
                    agent_run.id,
                    context_pack.context_pack.id,
                    context_pack.context_pack.pack_type.value,
                    context_pack_index + 1,
                    len(context_packs),
                )
                try:
                    generated_test_input = self._generate_for_context_pack(
                        agent_run.id,
                        generation_step,
                        context_pack,
                        context_pack_index=context_pack_index,
                    )
                except Exception as exc:
                    logger.error(
                        "test generation context pack failed agent_run_id=%s context_pack_id=%s "
                        "pack_type=%s position=%s/%s error_type=%s error=%s",
                        agent_run.id,
                        context_pack.context_pack.id,
                        context_pack.context_pack.pack_type.value,
                        context_pack_index + 1,
                        len(context_packs),
                        type(exc).__name__,
                        str(exc),
                    )
                    raise
                generated_test_inputs.append(generated_test_input)
                logger.info(
                    "test generation context pack succeeded agent_run_id=%s context_pack_id=%s "
                    "pack_type=%s position=%s/%s test_type=%s",
                    agent_run.id,
                    context_pack.context_pack.id,
                    context_pack.context_pack.pack_type.value,
                    context_pack_index + 1,
                    len(context_packs),
                    generated_test_input.test_payload.get("type"),
                )
            generation_step = self.agent_trace_repo.mark_step_finished(
                generation_step.id,
                status=AgentStatus.SUCCEEDED,
                output_summary={"generated_candidate_count": len(generated_test_inputs)},
            )

            persist_step = self._create_persist_step(
                agent_run.id,
                generated_test_inputs,
                parent_step_id=generation_step.id,
            )
            generated_tests = self.generated_test_repo.create_many(generated_test_inputs)
            persist_step = self.agent_trace_repo.mark_step_finished(
                persist_step.id,
                status=AgentStatus.SUCCEEDED,
                output_summary={"generated_test_count": len(generated_tests)},
            )
        except Exception:
            if generation_step is not None and generation_step.status != AgentStatus.SUCCEEDED:
                self.agent_trace_repo.mark_step_finished(
                    generation_step.id, status=AgentStatus.FAILED
                )
            if persist_step is not None and persist_step.status != AgentStatus.SUCCEEDED:
                self.agent_trace_repo.mark_step_finished(persist_step.id, status=AgentStatus.FAILED)
            self.agent_trace_repo.mark_run_finished(agent_run.id, status=AgentStatus.FAILED)
            raise

        agent_run = self.agent_trace_repo.mark_run_finished(
            agent_run.id,
            status=AgentStatus.SUCCEEDED,
        )
        logger.info(
            "test generation run succeeded agent_run_id=%s generated_test_count=%s",
            agent_run.id,
            len(generated_tests),
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
        deadline = LoopDeadline.start(self.max_loop_duration_seconds)
        context_pack_payload = self._read_context_pack(context_pack.context_pack.artifact_uri)
        context_pack_payload, generation_parent_step_id = self._inspect_context_pack(
            agent_run_id=agent_run_id,
            generation_step_id=generation_step.id,
            context_pack_id=context_pack.context_pack.id,
            context_pack_payload=context_pack_payload,
            context_pack_index=context_pack_index,
            deadline=deadline,
        )
        sequence_base = context_pack_index * CONTEXT_PACK_SEQUENCE_STRIDE
        loop_result = self.agent_loop.run(
            context_pack_payload,
            deadline=deadline,
            trace_branch=TraceBranch(
                agent_run_id=agent_run_id,
                parent_step_id=generation_parent_step_id,
                context_pack_id=context_pack.context_pack.id,
                sequence_base=sequence_base,
                artifact_prefix=f"{context_pack.context_pack.id}-generation",
            ),
        )
        final_turn = loop_result.turns[-1]
        if final_turn.model_trace is None or final_turn.model_trace.prompt_artifact is None:
            raise RuntimeError("generation model call did not record its prompt artifact")
        if final_turn.evidence_trace is None:
            raise RuntimeError("generation loop did not record its evidence verification step")
        prompt_artifact = final_turn.model_trace.prompt_artifact
        normalized_payload = loop_result.payload
        validated_artifact = self._write_validated_output_artifact(
            agent_run_id=agent_run_id,
            evidence_step_id=final_turn.evidence_trace.step.id,
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

    def _write_validated_output_artifact(
        self,
        *,
        agent_run_id: UUID,
        evidence_step_id: UUID,
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
                agent_step_id=evidence_step_id,
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
        return self.agent_trace_repo.mark_step_running(
            step.id,
            activity=AgentActivity.PLANNING,
        )

    def _create_persist_step(
        self,
        agent_run_id: UUID,
        generated_test_inputs: list[GeneratedTestInput],
        *,
        parent_step_id: UUID,
    ) -> AgentStepRead:
        step = self.agent_trace_repo.create_step(
            AgentStepInput(
                agent_run_id=agent_run_id,
                step_type=AgentStepType.PERSIST_RESULT,
                sequence=PERSIST_STEP_SEQUENCE,
                parent_step_id=parent_step_id,
                input_summary={"generated_candidate_count": len(generated_test_inputs)},
            )
        )
        return self.agent_trace_repo.mark_step_running(
            step.id,
            activity=AgentActivity.PERSISTING,
        )

    def _inspect_context_pack(
        self,
        *,
        agent_run_id: UUID,
        generation_step_id: UUID,
        context_pack_id: UUID,
        context_pack_payload: dict[str, Any],
        context_pack_index: int,
        deadline: LoopDeadline,
    ) -> tuple[dict[str, Any], UUID]:
        index_uri = snapshot_index_uri(context_pack_payload)
        if index_uri is None:
            return context_pack_payload, generation_step_id

        tool_context = RepositoryToolContext.from_index_uri(
            index_uri,
            snapshot_id=snapshot_id(context_pack_payload),
        )
        sequence_base = context_pack_index * CONTEXT_PACK_SEQUENCE_STRIDE
        plan_step = self._create_inspection_plan_step(
            agent_run_id=agent_run_id,
            context_pack_id=context_pack_id,
            context_pack_payload=context_pack_payload,
            sequence=sequence_base + PLAN_STEP_SEQUENCE_BASE,
            parent_step_id=generation_step_id,
        )
        try:
            result = self.repository_inspector.run(
                context_pack=context_pack_payload,
                tool_context=tool_context,
                deadline=deadline,
                agent_run_id=agent_run_id,
                tool_sequence_base=sequence_base + TOOL_CALL_SEQUENCE_BASE,
                trace_branch=TraceBranch(
                    agent_run_id=agent_run_id,
                    parent_step_id=plan_step.id,
                    context_pack_id=context_pack_id,
                    sequence_base=sequence_base,
                    artifact_prefix=f"{context_pack_id}-inspection",
                ),
            )
        except Exception as exc:
            self.agent_trace_repo.mark_step_finished(
                plan_step.id,
                status=AgentStatus.FAILED,
                warning_summary=[{"error_type": type(exc).__name__, "error": str(exc)}],
            )
            raise

        self.agent_trace_repo.mark_step_finished(
            plan_step.id,
            status=AgentStatus.SUCCEEDED,
            output_summary=self._inspection_output_summary(result),
            warning_summary=self._inspection_warnings(result),
        )
        last_model_step_id = plan_step.id
        if result.turns and result.turns[-1].model_trace is not None:
            model_handle = result.turns[-1].model_trace.handle
            if model_handle is not None:
                last_model_step_id = model_handle.step.id
        return (
            context_pack_with_tool_inspection(
                context_pack_payload,
                result.prompt_payload(max_bytes=self.max_tool_observation_bytes),
            ),
            last_model_step_id,
        )

    def _create_inspection_plan_step(
        self,
        *,
        agent_run_id: UUID,
        context_pack_id: UUID,
        context_pack_payload: dict[str, Any],
        sequence: int,
        parent_step_id: UUID,
    ) -> AgentStepRead:
        step = self.agent_trace_repo.create_step(
            AgentStepInput(
                agent_run_id=agent_run_id,
                step_type=AgentStepType.BUILD_TEST_PLAN,
                sequence=sequence,
                parent_step_id=parent_step_id,
                context_pack_id=context_pack_id,
                input_summary={
                    "context_pack_id": str(context_pack_id),
                    "pack_type": context_pack_payload.get("pack_type"),
                    "model": self.model,
                    "prompt_version": INSPECTION_PROMPT_VERSION,
                    "max_duration_seconds": self.max_loop_duration_seconds,
                },
            )
        )
        return self.agent_trace_repo.mark_step_running(
            step.id,
            activity=AgentActivity.PLANNING,
        )

    @staticmethod
    def _inspection_output_summary(result: RepositoryInspectionResult) -> dict[str, Any]:
        return {
            "completion_reason": result.completion_reason,
            "model_turn_count": len(result.turns),
            "completed_tool_call_count": len(result.executions),
            "failed_tool_call_count": len(result.failed_calls),
            "duplicate_tool_call_count": len(result.duplicate_calls),
        }

    @staticmethod
    def _inspection_warnings(result: RepositoryInspectionResult) -> list[dict[str, Any]]:
        warnings = [
            {
                "type": "tool_call_failed",
                "call_id": failed.call_id,
                "tool_name": failed.tool_name,
                "error": failed.error,
            }
            for failed in result.failed_calls
        ]
        warnings.extend(
            {
                "type": "duplicate_tool_call_rejected",
                "call_id": duplicate.call_id,
                "tool_name": duplicate.tool_name,
                "arguments": duplicate.arguments,
            }
            for duplicate in result.duplicate_calls
        )
        return warnings

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
