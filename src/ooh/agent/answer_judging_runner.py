from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from ooh.agent.answer_judging import AnswerJudgingLoop, AnswerJudgingTurn
from ooh.agent.artifacts import AgentArtifactStore
from ooh.agent.providers import ModelProvider
from ooh.db.models import (
    AgentArtifactRead,
    AgentArtifactType,
    AgentRunRead,
    AgentRunType,
    AgentStatus,
    AgentStepRead,
    AgentStepType,
    GeneratedTestRead,
    ProvenanceRefType,
    TestAnswerRead,
    TestResultRead,
    TestResultStatus,
)
from ooh.db.repos import (
    AgentArtifactInput,
    AgentRunInput,
    AgentStepInput,
    AgentTraceRepo,
    ProvenanceRefInput,
    SavedLearningInput,
    SavedLearningRepo,
    TestResultInput,
    TestResultRepo,
)


@dataclass(frozen=True)
class AnswerJudgingRunResult:
    agent_run: AgentRunRead
    test_result: TestResultRead


class AnswerJudgingRunService:
    def __init__(
        self,
        *,
        provider: ModelProvider,
        model: str,
        artifact_store: AgentArtifactStore,
        agent_trace_repo: AgentTraceRepo,
        test_result_repo: TestResultRepo,
        saved_learning_repo: SavedLearningRepo,
    ) -> None:
        self.provider = provider
        self.model = model
        self.artifact_store = artifact_store
        self.agent_trace_repo = agent_trace_repo
        self.test_result_repo = test_result_repo
        self.saved_learning_repo = saved_learning_repo
        self.judge_loop = AnswerJudgingLoop(provider, model=model)

    def judge_answer(
        self,
        *,
        generated_test: GeneratedTestRead,
        test_answer: TestAnswerRead,
        job_id: UUID | None = None,
    ) -> AnswerJudgingRunResult:
        agent_run = self.agent_trace_repo.create_run(
            AgentRunInput(
                run_type=AgentRunType.ANSWER_JUDGING,
                job_id=job_id,
                repository_id=generated_test.repository_id,
                model_profile=self.model,
            )
        )
        agent_run = self.agent_trace_repo.mark_run_running(agent_run.id)

        judge_step: AgentStepRead | None = None
        persist_step: AgentStepRead | None = None
        try:
            judge_step = self._create_judge_step(agent_run.id, generated_test, test_answer)
            loop_result = self.judge_loop.run(
                test_payload=generated_test.test_payload,
                answer_payload=test_answer.answer_payload,
                evidence_refs=generated_test.evidence_refs,
            )
            prompt_artifact = self._write_turn_artifacts(
                agent_run_id=agent_run.id,
                judge_step_id=judge_step.id,
                generated_test_id=generated_test.id,
                test_answer_id=test_answer.id,
                turns=loop_result.turns,
            )
            validated_artifact = self._write_validated_output_artifact(
                agent_run_id=agent_run.id,
                judge_step_id=judge_step.id,
                generated_test_id=generated_test.id,
                test_answer_id=test_answer.id,
                payload=loop_result.payload,
            )
            self._record_provenance(
                validated_artifact=validated_artifact,
                prompt_artifact=prompt_artifact,
                generated_test=generated_test,
                model=loop_result.turns[-1].model_response.model,
            )
            judge_step = self.agent_trace_repo.mark_step_finished(
                judge_step.id,
                status=AgentStatus.SUCCEEDED,
                output_summary={
                    "score": loop_result.payload["score"],
                    "status": loop_result.payload["status"],
                },
            )

            persist_step = self._create_persist_step(agent_run.id, loop_result.payload)
            test_result = self.test_result_repo.create(
                TestResultInput(
                    generated_test_id=generated_test.id,
                    test_answer_id=test_answer.id,
                    agent_run_id=agent_run.id,
                    score=self._score_decimal(loop_result.payload["score"]),
                    status=TestResultStatus(loop_result.payload["status"]),
                    feedback=self._feedback_payload(loop_result.payload, loop_result.prompt_version),
                    alert_flag=loop_result.payload["status"] != TestResultStatus.PASSING.value,
                )
            )
            self._save_suggested_learning(
                generated_test=generated_test,
                test_result=test_result,
                judge_payload=loop_result.payload,
            )
            persist_step = self.agent_trace_repo.mark_step_finished(
                persist_step.id,
                status=AgentStatus.SUCCEEDED,
                output_summary={"test_result_id": str(test_result.id)},
            )
        except Exception:
            if judge_step is not None and judge_step.status != AgentStatus.SUCCEEDED:
                self.agent_trace_repo.mark_step_finished(judge_step.id, status=AgentStatus.FAILED)
            if persist_step is not None and persist_step.status != AgentStatus.SUCCEEDED:
                self.agent_trace_repo.mark_step_finished(persist_step.id, status=AgentStatus.FAILED)
            self.agent_trace_repo.mark_run_finished(agent_run.id, status=AgentStatus.FAILED)
            raise

        agent_run = self.agent_trace_repo.mark_run_finished(agent_run.id, status=AgentStatus.SUCCEEDED)
        return AnswerJudgingRunResult(agent_run=agent_run, test_result=test_result)

    def _write_turn_artifacts(
        self,
        *,
        agent_run_id: UUID,
        judge_step_id: UUID,
        generated_test_id: UUID,
        test_answer_id: UUID,
        turns: list[AnswerJudgingTurn],
    ) -> AgentArtifactRead:
        prompt_artifact: AgentArtifactRead | None = None
        for turn in turns:
            prompt_artifact = self._write_prompt_artifact(
                agent_run_id=agent_run_id,
                judge_step_id=judge_step_id,
                generated_test_id=generated_test_id,
                test_answer_id=test_answer_id,
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
                judge_step_id=judge_step_id,
                generated_test_id=generated_test_id,
                test_answer_id=test_answer_id,
                turn=turn,
                response_payload={
                    "model": turn.model_response.model,
                    "content": turn.model_response.content,
                    "finish_reason": turn.model_response.finish_reason,
                    "raw_response": turn.model_response.raw_response,
                    "validation_error": turn.validation_error,
                },
            )
            if turn.validation_error is not None:
                self._write_validation_error_artifact(
                    agent_run_id=agent_run_id,
                    judge_step_id=judge_step_id,
                    generated_test_id=generated_test_id,
                    test_answer_id=test_answer_id,
                    turn=turn,
                )

        if prompt_artifact is None:
            raise ValueError("answer judging loop produced no turns")
        return prompt_artifact

    def _write_prompt_artifact(
        self,
        *,
        agent_run_id: UUID,
        judge_step_id: UUID,
        generated_test_id: UUID,
        test_answer_id: UUID,
        turn: AnswerJudgingTurn,
        request_payload: dict[str, Any],
    ) -> AgentArtifactRead:
        stored_artifact = self.artifact_store.write_json(
            agent_run_id=agent_run_id,
            file_name=(
                f"{generated_test_id}-{test_answer_id}-turn-"
                f"{turn.sequence}-{turn.action}-prompt.json"
            ),
            payload=request_payload,
        )
        return self.agent_trace_repo.create_artifact(
            AgentArtifactInput(
                agent_step_id=judge_step_id,
                artifact_type=AgentArtifactType.PROMPT,
                artifact_uri=stored_artifact.artifact_uri,
                content_hash=stored_artifact.content_hash,
            )
        )

    def _write_raw_response_artifact(
        self,
        *,
        agent_run_id: UUID,
        judge_step_id: UUID,
        generated_test_id: UUID,
        test_answer_id: UUID,
        turn: AnswerJudgingTurn,
        response_payload: dict[str, Any],
    ) -> AgentArtifactRead:
        stored_artifact = self.artifact_store.write_json(
            agent_run_id=agent_run_id,
            file_name=(
                f"{generated_test_id}-{test_answer_id}-turn-"
                f"{turn.sequence}-{turn.action}-raw-response.json"
            ),
            payload=response_payload,
        )
        return self.agent_trace_repo.create_artifact(
            AgentArtifactInput(
                agent_step_id=judge_step_id,
                artifact_type=AgentArtifactType.RAW_MODEL_RESPONSE,
                artifact_uri=stored_artifact.artifact_uri,
                content_hash=stored_artifact.content_hash,
            )
        )

    def _write_validation_error_artifact(
        self,
        *,
        agent_run_id: UUID,
        judge_step_id: UUID,
        generated_test_id: UUID,
        test_answer_id: UUID,
        turn: AnswerJudgingTurn,
    ) -> AgentArtifactRead:
        stored_artifact = self.artifact_store.write_json(
            agent_run_id=agent_run_id,
            file_name=(
                f"{generated_test_id}-{test_answer_id}-turn-"
                f"{turn.sequence}-{turn.action}-validation-error.json"
            ),
            payload={
                "turn_sequence": turn.sequence,
                "action": turn.action,
                "validation_error": turn.validation_error,
            },
        )
        return self.agent_trace_repo.create_artifact(
            AgentArtifactInput(
                agent_step_id=judge_step_id,
                artifact_type=AgentArtifactType.TRACE,
                artifact_uri=stored_artifact.artifact_uri,
                content_hash=stored_artifact.content_hash,
            )
        )

    def _write_validated_output_artifact(
        self,
        *,
        agent_run_id: UUID,
        judge_step_id: UUID,
        generated_test_id: UUID,
        test_answer_id: UUID,
        payload: dict[str, Any],
    ) -> AgentArtifactRead:
        stored_artifact = self.artifact_store.write_json(
            agent_run_id=agent_run_id,
            file_name=f"{generated_test_id}-{test_answer_id}-validated-judgment.json",
            payload=payload,
        )
        return self.agent_trace_repo.create_artifact(
            AgentArtifactInput(
                agent_step_id=judge_step_id,
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
        generated_test: GeneratedTestRead,
        model: str,
    ) -> None:
        self.agent_trace_repo.create_provenance_ref(
            ProvenanceRefInput(
                artifact_id=validated_artifact.id,
                ref_type=ProvenanceRefType.CONTEXT_PACK,
                ref_uri=f"generated_test:{generated_test.id}",
                content_hash=None,
                metadata={"context_pack_id": str(generated_test.context_pack_id)},
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

    def _create_judge_step(
        self,
        agent_run_id: UUID,
        generated_test: GeneratedTestRead,
        test_answer: TestAnswerRead,
    ) -> AgentStepRead:
        step = self.agent_trace_repo.create_step(
            AgentStepInput(
                agent_run_id=agent_run_id,
                step_type=AgentStepType.JUDGE_ANSWER,
                sequence=1,
                input_summary={
                    "generated_test_id": str(generated_test.id),
                    "test_answer_id": str(test_answer.id),
                },
            )
        )
        return self.agent_trace_repo.mark_step_running(step.id)

    def _create_persist_step(
        self,
        agent_run_id: UUID,
        judge_payload: dict[str, Any],
    ) -> AgentStepRead:
        step = self.agent_trace_repo.create_step(
            AgentStepInput(
                agent_run_id=agent_run_id,
                step_type=AgentStepType.PERSIST_RESULT,
                sequence=2,
                input_summary={
                    "score": judge_payload["score"],
                    "status": judge_payload["status"],
                },
            )
        )
        return self.agent_trace_repo.mark_step_running(step.id)

    def _save_suggested_learning(
        self,
        *,
        generated_test: GeneratedTestRead,
        test_result: TestResultRead,
        judge_payload: dict[str, Any],
    ) -> None:
        suggested_learning = judge_payload.get("suggested_learning")
        if not isinstance(suggested_learning, dict):
            return

        self.saved_learning_repo.create(
            SavedLearningInput(
                repository_id=generated_test.repository_id,
                test_result_id=test_result.id,
                title=str(suggested_learning["title"]),
                summary=str(suggested_learning["summary"]),
                source_payload={
                    "generated_test_id": str(generated_test.id),
                    "judge_payload": judge_payload,
                },
            )
        )

    @staticmethod
    def _score_decimal(score: float) -> Decimal:
        return Decimal(str(score)).quantize(Decimal("0.0001"))

    @staticmethod
    def _feedback_payload(judge_payload: dict[str, Any], prompt_version: str) -> dict[str, Any]:
        return {
            "feedback": judge_payload["feedback"],
            "missed_concepts": judge_payload.get("missed_concepts", []),
            "evidence_refs": judge_payload.get("evidence_refs", []),
            "suggested_learning": judge_payload.get("suggested_learning"),
            "prompt_version": prompt_version,
        }
