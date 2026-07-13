from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from ooh.agent.artifacts import AgentArtifactStore
from ooh.agent.providers import ModelProvider
from ooh.agent.test_generation import (
    TEST_GENERATION_PROMPT_VERSION,
    build_test_generation_request,
    parse_generated_test_payload,
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
            generated_test_inputs = [
                self._generate_for_context_pack(agent_run.id, generation_step, context_pack)
                for context_pack in context_packs
            ]
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
    ) -> GeneratedTestInput:
        context_pack_payload = self._read_context_pack(context_pack.context_pack.artifact_uri)
        request = build_test_generation_request(model=self.model, context_pack=context_pack_payload)
        prompt_artifact = self._write_prompt_artifact(
            agent_run_id=agent_run_id,
            generation_step_id=generation_step.id,
            context_pack_id=context_pack.context_pack.id,
            request_payload={
                "model": request.model,
                "messages": [
                    {"role": message.role, "content": message.content}
                    for message in request.messages
                ],
                "response_format": request.response_format,
                "temperature": request.temperature,
                "metadata": request.metadata,
            },
        )
        model_response = self.provider.generate(request)
        self._write_raw_response_artifact(
            agent_run_id=agent_run_id,
            generation_step_id=generation_step.id,
            context_pack_id=context_pack.context_pack.id,
            response_payload={
                "model": model_response.model,
                "content": model_response.content,
                "finish_reason": model_response.finish_reason,
                "raw_response": model_response.raw_response,
            },
        )
        normalized_payload = parse_generated_test_payload(model_response.content)
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
            model=model_response.model,
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
            prompt_version=TEST_GENERATION_PROMPT_VERSION,
        )

    def _write_prompt_artifact(
        self,
        *,
        agent_run_id: UUID,
        generation_step_id: UUID,
        context_pack_id: UUID,
        request_payload: dict[str, Any],
    ) -> AgentArtifactRead:
        stored_artifact = self.artifact_store.write_json(
            agent_run_id=agent_run_id,
            file_name=f"{context_pack_id}-prompt.json",
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
        response_payload: dict[str, Any],
    ) -> AgentArtifactRead:
        stored_artifact = self.artifact_store.write_json(
            agent_run_id=agent_run_id,
            file_name=f"{context_pack_id}-raw-response.json",
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
                sequence=1,
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
                sequence=2,
                input_summary={"generated_candidate_count": len(generated_test_inputs)},
            )
        )
        return self.agent_trace_repo.mark_step_running(step.id)

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
