from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from ooh.agent.contracts import GeneratedTestGradingPayload, SubmittedAnswerPayload
from ooh.db.models import SavedLearningRead, TestAnswerRead, TestResultRead, TestResultStatus
from ooh.db.models.jobs import JobRead, JobStatus, JobType


TestAnswerSubmitRequest = SubmittedAnswerPayload


class JobResponse(BaseModel):
    id: UUID
    repository_id: UUID | None
    job_type: JobType
    status: JobStatus
    result_metadata: dict[str, Any]
    created_at: datetime

    @classmethod
    def from_record(cls, job: JobRead) -> "JobResponse":
        return cls(
            id=job.id,
            repository_id=job.repository_id,
            job_type=job.job_type,
            status=job.status,
            result_metadata=job.result_metadata,
            created_at=job.created_at,
        )


class TestAnswerResponse(BaseModel):
    id: UUID
    generated_test_id: UUID
    answer_payload: dict[str, Any]
    submitted_at: datetime

    @classmethod
    def from_record(cls, answer: TestAnswerRead) -> "TestAnswerResponse":
        return cls(
            id=answer.id,
            generated_test_id=answer.generated_test_id,
            answer_payload=answer.answer_payload,
            submitted_at=answer.submitted_at,
        )


class TestAnswerSubmissionResponse(BaseModel):
    answer: TestAnswerResponse
    judge_job: JobResponse


class TestResultResponse(BaseModel):
    id: UUID
    generated_test_id: UUID
    test_answer_id: UUID | None
    agent_run_id: UUID | None
    score: Decimal
    status: TestResultStatus
    feedback: dict[str, Any]
    grading_guidance: GeneratedTestGradingPayload | None = None
    alert_flag: bool
    created_at: datetime

    @classmethod
    def from_record(
        cls,
        result: TestResultRead,
        *,
        grading_guidance: dict[str, Any] | None = None,
    ) -> "TestResultResponse":
        return cls(
            id=result.id,
            generated_test_id=result.generated_test_id,
            test_answer_id=result.test_answer_id,
            agent_run_id=result.agent_run_id,
            score=result.score,
            status=result.status,
            feedback=result.feedback,
            grading_guidance=grading_guidance,
            alert_flag=result.alert_flag,
            created_at=result.created_at,
        )


class SavedLearningResponse(BaseModel):
    id: UUID
    repository_id: UUID
    test_result_id: UUID | None
    title: str
    summary: str
    source_payload: dict[str, Any]
    created_at: datetime

    @classmethod
    def from_record(cls, learning: SavedLearningRead) -> "SavedLearningResponse":
        return cls(
            id=learning.id,
            repository_id=learning.repository_id,
            test_result_id=learning.test_result_id,
            title=learning.title,
            summary=learning.summary,
            source_payload=learning.source_payload,
            created_at=learning.created_at,
        )
