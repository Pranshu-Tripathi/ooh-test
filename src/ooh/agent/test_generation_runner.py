from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
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
    ContextPackType,
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
from ooh.generation_config import (
    MAX_GENERATION_QUESTIONS_PER_CATEGORY,
    MAX_GENERATION_QUESTIONS_PER_JOB,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GeneratedTestRunResult:
    agent_run: AgentRunRead
    generated_tests: list[GeneratedTestRead]
    requested_question_count: int = 0
    failures: list["GeneratedQuestionFailure"] = field(default_factory=list)

    @property
    def failed_question_count(self) -> int:
        return len(self.failures)


@dataclass(frozen=True)
class GeneratedQuestionFailure:
    category: GeneratedTestCategory
    context_pack_id: UUID
    question_number: int
    question_count: int
    error_type: str
    reason: str
    attempt_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "context_pack_id": str(self.context_pack_id),
            "question_number": self.question_number,
            "question_count": self.question_count,
            "error_type": self.error_type,
            "reason": self.reason,
            "attempt_count": self.attempt_count,
        }


PLAN_STEP_SEQUENCE_BASE = 10
TOOL_CALL_SEQUENCE_BASE = 1_000
CONTEXT_PACK_SEQUENCE_STRIDE = 1_000_000
QUESTION_SEQUENCE_STRIDE = 150_000
DUPLICATE_RETRY_SEQUENCE_STRIDE = 50_000
DEDUPE_STEP_SEQUENCE_OFFSET = 100_090
GENERATION_STEP_SEQUENCE = 1
PERSIST_STEP_SEQUENCE = 20_000_000
MAX_DUPLICATE_REGENERATIONS = 1
NEAR_DUPLICATE_TOKEN_SIMILARITY = 0.85


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
        question_counts: Mapping[ContextPackType, int] | None = None,
    ) -> GeneratedTestRunResult:
        repository_id = self._repository_id(context_packs)
        normalized_question_counts = self._question_counts(context_packs, question_counts)
        requested_question_count = sum(normalized_question_counts.values())
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

        generation_step = self._create_generation_step(
            agent_run.id,
            context_packs,
            normalized_question_counts,
        )
        generated_test_inputs: list[GeneratedTestInput] = []
        failures: list[GeneratedQuestionFailure] = []
        successful_questions: list[str] = []
        prior_questions: list[str] = []
        last_question_error: Exception | None = None

        try:
            prior_questions = self._prior_questions(repository_id)
            for context_pack_index, context_pack in enumerate(context_packs):
                question_count = normalized_question_counts[
                    context_pack.context_pack.pack_type
                ]
                logger.info(
                    "test generation context pack started agent_run_id=%s context_pack_id=%s "
                    "pack_type=%s position=%s/%s question_count=%s",
                    agent_run.id,
                    context_pack.context_pack.id,
                    context_pack.context_pack.pack_type.value,
                    context_pack_index + 1,
                    len(context_packs),
                    question_count,
                )

                context_pack_payload = self._read_context_pack(
                    context_pack.context_pack.artifact_uri
                )
                context_pack_payload, generation_parent_step_id = self._inspect_context_pack(
                    agent_run_id=agent_run.id,
                    generation_step_id=generation_step.id,
                    context_pack_id=context_pack.context_pack.id,
                    context_pack_payload=context_pack_payload,
                    context_pack_index=context_pack_index,
                    deadline=LoopDeadline.start(self.max_loop_duration_seconds),
                )

                for question_index in range(question_count):
                    question_number = question_index + 1
                    logger.info(
                        "test generation question started agent_run_id=%s context_pack_id=%s "
                        "pack_type=%s question=%s/%s",
                        agent_run.id,
                        context_pack.context_pack.id,
                        context_pack.context_pack.pack_type.value,
                        question_number,
                        question_count,
                    )
                    excluded_questions = successful_questions + prior_questions
                    try:
                        generated_test_input = self._generate_question(
                            agent_run_id=agent_run.id,
                            generation_parent_step_id=generation_parent_step_id,
                            context_pack=context_pack,
                            context_pack_payload=context_pack_payload,
                            context_pack_index=context_pack_index,
                            question_index=question_index,
                            question_count=question_count,
                            excluded_questions=excluded_questions,
                        )
                    except Exception as exc:
                        last_question_error = exc
                        failure = self._question_failure(
                            context_pack=context_pack,
                            question_number=question_number,
                            question_count=question_count,
                            exc=exc,
                        )
                        failures.append(failure)
                        logger.error(
                            "test generation question failed agent_run_id=%s context_pack_id=%s "
                            "pack_type=%s question=%s/%s error_type=%s error=%s",
                            agent_run.id,
                            context_pack.context_pack.id,
                            context_pack.context_pack.pack_type.value,
                            question_number,
                            question_count,
                            type(exc).__name__,
                            str(exc),
                        )
                        continue

                    generated_test_inputs.append(generated_test_input)
                    question = generated_test_input.test_payload.get("question")
                    if isinstance(question, str):
                        successful_questions.append(question)
                    logger.info(
                        "test generation question succeeded agent_run_id=%s context_pack_id=%s "
                        "pack_type=%s question=%s/%s test_type=%s",
                        agent_run.id,
                        context_pack.context_pack.id,
                        context_pack.context_pack.pack_type.value,
                        question_number,
                        question_count,
                        generated_test_input.test_payload.get("type"),
                    )
        except Exception:
            self.agent_trace_repo.mark_step_finished(
                generation_step.id,
                status=AgentStatus.FAILED,
                output_summary={
                    "requested_question_count": requested_question_count,
                    "generated_candidate_count": len(generated_test_inputs),
                    "failed_question_count": len(failures),
                },
                warning_summary=[failure.to_dict() for failure in failures],
            )
            self.agent_trace_repo.mark_run_finished(agent_run.id, status=AgentStatus.FAILED)
            raise

        if not generated_test_inputs:
            generation_step = self.agent_trace_repo.mark_step_finished(
                generation_step.id,
                status=AgentStatus.FAILED,
                output_summary={
                    "requested_question_count": requested_question_count,
                    "generated_candidate_count": 0,
                    "failed_question_count": len(failures),
                },
                warning_summary=[failure.to_dict() for failure in failures],
            )
            agent_run = self.agent_trace_repo.mark_run_finished(
                agent_run.id,
                status=AgentStatus.FAILED,
            )
            run_result = GeneratedTestRunResult(
                agent_run=agent_run,
                generated_tests=[],
                requested_question_count=requested_question_count,
                failures=failures,
            )
            error = last_question_error or RuntimeError("no valid question was generated")
            setattr(error, "generation_run_result", run_result)
            raise error

        generation_step = self.agent_trace_repo.mark_step_finished(
            generation_step.id,
            status=AgentStatus.SUCCEEDED,
            output_summary={
                "requested_question_count": requested_question_count,
                "generated_candidate_count": len(generated_test_inputs),
                "failed_question_count": len(failures),
            },
            warning_summary=[failure.to_dict() for failure in failures],
        )

        persist_step = self._create_persist_step(
            agent_run.id,
            generated_test_inputs,
            parent_step_id=generation_step.id,
        )
        try:
            generated_tests = self.generated_test_repo.create_many(generated_test_inputs)
        except Exception:
            self.agent_trace_repo.mark_step_finished(
                persist_step.id,
                status=AgentStatus.FAILED,
            )
            self.agent_trace_repo.mark_run_finished(agent_run.id, status=AgentStatus.FAILED)
            raise
        self.agent_trace_repo.mark_step_finished(
            persist_step.id,
            status=AgentStatus.SUCCEEDED,
            output_summary={"generated_test_count": len(generated_tests)},
        )

        agent_run = self.agent_trace_repo.mark_run_finished(
            agent_run.id,
            status=AgentStatus.SUCCEEDED,
        )
        logger.info(
            "test generation run succeeded agent_run_id=%s requested_question_count=%s "
            "generated_test_count=%s failed_question_count=%s",
            agent_run.id,
            requested_question_count,
            len(generated_tests),
            len(failures),
        )
        return GeneratedTestRunResult(
            agent_run=agent_run,
            generated_tests=generated_tests,
            requested_question_count=requested_question_count,
            failures=failures,
        )

    def _generate_question(
        self,
        *,
        agent_run_id: UUID,
        generation_parent_step_id: UUID,
        context_pack: ContextPackWithSources,
        context_pack_payload: dict[str, Any],
        context_pack_index: int,
        question_index: int,
        question_count: int,
        excluded_questions: list[str],
    ) -> GeneratedTestInput:
        question_number = question_index + 1
        retry_exclusions = list(excluded_questions)
        dedupe_parent_step_id = generation_parent_step_id
        loop_result = None
        for duplicate_attempt in range(MAX_DUPLICATE_REGENERATIONS + 1):
            sequence_base = (
                context_pack_index * CONTEXT_PACK_SEQUENCE_STRIDE
                + question_index * QUESTION_SEQUENCE_STRIDE
                + duplicate_attempt * DUPLICATE_RETRY_SEQUENCE_STRIDE
            )
            loop_result = self.agent_loop.run(
                context_pack_payload,
                deadline=LoopDeadline.start(self.max_loop_duration_seconds),
                question_number=question_number,
                question_count=question_count,
                excluded_questions=retry_exclusions,
                trace_branch=TraceBranch(
                    agent_run_id=agent_run_id,
                    parent_step_id=dedupe_parent_step_id,
                    context_pack_id=context_pack.context_pack.id,
                    sequence_base=sequence_base,
                    artifact_prefix=(
                        f"{context_pack.context_pack.id}-generation"
                        if question_count == 1 and duplicate_attempt == 0
                        else (
                            f"{context_pack.context_pack.id}-question-{question_number}"
                            f"-attempt-{duplicate_attempt + 1}"
                        )
                    ),
                ),
            )
            normalized_payload = loop_result.payload
            question = normalized_payload.get("question")
            if not isinstance(question, str):
                raise RuntimeError("validated generated test does not contain a question")

            duplicate_question = find_duplicate_question(question, retry_exclusions)
            if duplicate_question is None:
                break

            evidence_trace = loop_result.turns[-1].evidence_trace
            if evidence_trace is None:
                raise RuntimeError("generation loop did not record its evidence verification step")
            dedupe_step = self._record_duplicate_step(
                agent_run_id=agent_run_id,
                parent_step_id=evidence_trace.step.id,
                context_pack_id=context_pack.context_pack.id,
                sequence=sequence_base + DEDUPE_STEP_SEQUENCE_OFFSET,
                question_number=question_number,
                duplicate_attempt=duplicate_attempt + 1,
                question=question,
                duplicate_question=duplicate_question,
            )
            dedupe_parent_step_id = dedupe_step.id
            retry_exclusions.append(question)
            if duplicate_attempt >= MAX_DUPLICATE_REGENERATIONS:
                error = ValueError(
                    "generated question duplicates a recent question after "
                    f"{duplicate_attempt + 1} regeneration attempts"
                )
                setattr(error, "question_attempt_count", duplicate_attempt + 1)
                raise error
        if loop_result is None:
            raise RuntimeError("generation loop did not run")

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
            question_number=question_number,
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
        question_number: int,
        payload: dict[str, Any],
    ) -> AgentArtifactRead:
        stored_artifact = self.artifact_store.write_json(
            agent_run_id=agent_run_id,
            file_name=f"{context_pack_id}-question-{question_number}-validated-output.json",
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
        question_counts: Mapping[ContextPackType, int],
    ) -> AgentStepRead:
        step = self.agent_trace_repo.create_step(
            AgentStepInput(
                agent_run_id=agent_run_id,
                step_type=AgentStepType.GENERATE_QUESTIONS,
                sequence=GENERATION_STEP_SEQUENCE,
                input_summary={
                    "context_pack_count": len(context_packs),
                    "requested_question_count": sum(question_counts.values()),
                    "generation_plan": [
                        {
                            "category": context_pack.context_pack.pack_type.value,
                            "question_count": question_counts[
                                context_pack.context_pack.pack_type
                            ],
                        }
                        for context_pack in context_packs
                    ],
                    "execution_mode": "sequential",
                },
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

    def _record_duplicate_step(
        self,
        *,
        agent_run_id: UUID,
        parent_step_id: UUID,
        context_pack_id: UUID,
        sequence: int,
        question_number: int,
        duplicate_attempt: int,
        question: str,
        duplicate_question: str,
    ) -> AgentStepRead:
        step = self.agent_trace_repo.create_step(
            AgentStepInput(
                agent_run_id=agent_run_id,
                step_type=AgentStepType.DEDUPE_AND_BALANCE,
                sequence=sequence,
                parent_step_id=parent_step_id,
                context_pack_id=context_pack_id,
                iteration=duplicate_attempt,
                input_summary={
                    "question_number": question_number,
                    "candidate_question": question,
                },
            )
        )
        step = self.agent_trace_repo.mark_step_running(
            step.id,
            activity=AgentActivity.VALIDATING,
        )
        return self.agent_trace_repo.mark_step_finished(
            step.id,
            status=AgentStatus.FAILED,
            output_summary={
                "duplicate": True,
                "matched_question": duplicate_question,
                "regeneration_scheduled": duplicate_attempt <= MAX_DUPLICATE_REGENERATIONS,
            },
            warning_summary=[
                {
                    "type": "duplicate_question",
                    "matched_question": duplicate_question,
                }
            ],
        )

    def _prior_questions(self, repository_id: UUID) -> list[str]:
        list_for_repository = getattr(self.generated_test_repo, "list_for_repository", None)
        if list_for_repository is None:
            return []
        generated_tests = list_for_repository(repository_id, limit=50)
        questions: list[str] = []
        for generated_test in generated_tests:
            question = generated_test.test_payload.get("question")
            if isinstance(question, str) and question.strip():
                questions.append(question.strip())
        return list(dict.fromkeys(questions))

    @staticmethod
    def _question_failure(
        *,
        context_pack: ContextPackWithSources,
        question_number: int,
        question_count: int,
        exc: Exception,
    ) -> GeneratedQuestionFailure:
        turns = getattr(exc, "turns", None)
        attempt_count = (
            len(turns)
            if isinstance(turns, list) and turns
            else getattr(exc, "question_attempt_count", 1)
        )
        return GeneratedQuestionFailure(
            category=GeneratedTestCategory(context_pack.context_pack.pack_type.value),
            context_pack_id=context_pack.context_pack.id,
            question_number=question_number,
            question_count=question_count,
            error_type=type(exc).__name__,
            reason=str(exc)[:1_000],
            attempt_count=attempt_count,
        )

    @staticmethod
    def _question_counts(
        context_packs: list[ContextPackWithSources],
        question_counts: Mapping[ContextPackType, int] | None,
    ) -> dict[ContextPackType, int]:
        pack_types = [context_pack.context_pack.pack_type for context_pack in context_packs]
        if len(set(pack_types)) != len(pack_types):
            raise ValueError("context packs must contain unique pack types")
        normalized = (
            {pack_type: 1 for pack_type in pack_types}
            if question_counts is None
            else dict(question_counts)
        )
        if set(normalized) != set(pack_types):
            raise ValueError("question counts must match the selected context pack types")
        for pack_type, question_count in normalized.items():
            if not isinstance(pack_type, ContextPackType):
                raise ValueError("question count keys must be context pack types")
            if isinstance(question_count, bool) or not isinstance(question_count, int):
                raise ValueError("question counts must be integers")
            if question_count < 1 or question_count > MAX_GENERATION_QUESTIONS_PER_CATEGORY:
                raise ValueError(
                    "question count per category must be between "
                    f"1 and {MAX_GENERATION_QUESTIONS_PER_CATEGORY}"
                )
        if sum(normalized.values()) > MAX_GENERATION_QUESTIONS_PER_JOB:
            raise ValueError(
                "generation run cannot request more than "
                f"{MAX_GENERATION_QUESTIONS_PER_JOB} questions"
            )
        return normalized

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


def find_duplicate_question(question: str, prior_questions: list[str]) -> str | None:
    normalized_question = _normalized_question(question)
    question_tokens = set(normalized_question.split())
    for prior_question in prior_questions:
        normalized_prior = _normalized_question(prior_question)
        if not normalized_prior:
            continue
        if normalized_question == normalized_prior:
            return prior_question

        prior_tokens = set(normalized_prior.split())
        if min(len(question_tokens), len(prior_tokens)) < 5:
            continue
        token_similarity = (
            2 * len(question_tokens & prior_tokens)
            / (len(question_tokens) + len(prior_tokens))
        )
        if token_similarity >= NEAR_DUPLICATE_TOKEN_SIMILARITY:
            return prior_question
    return None


def _normalized_question(question: str) -> str:
    return " ".join(re.findall(r"\w+", question.casefold()))
