import logging
from typing import Any
from uuid import UUID

from ooh.config import get_settings
from ooh.db import Database
from ooh.db.models import ContextPackType, JobRead, JobStatus, JobType
from ooh.services import build_worker_services
from ooh.services.answer_judging import payload_uuid
from ooh.services.test_generation import requested_pack_types_from_job

logger = logging.getLogger(__name__)
JobResultMetadata = dict[str, Any]


class JobRunner:
    def __init__(self, db: Database, *, worker_id: str) -> None:
        settings = get_settings()
        self.worker_id = worker_id
        services = build_worker_services(db, settings=settings)
        self.job_service = services.jobs
        self.repository_service = services.repositories
        self.test_generation_service = services.test_generation
        self.answer_judging_service = services.answer_judging

    def process_once(self) -> bool:
        job = self.job_service.claim_next(worker_id=self.worker_id)
        if job is None:
            return False

        logger.info(
            "claimed job job_id=%s job_type=%s repository_id=%s attempt=%s/%s worker_id=%s payload=%s",
            job.id,
            job.job_type.value,
            job.repository_id,
            job.attempt_count,
            job.max_attempts,
            self.worker_id,
            job.payload,
        )

        try:
            result_metadata = self.dispatch(job)
        except Exception as exc:
            result_metadata = self._failure_metadata(job, exc)
            logger.exception(
                "job failed job_id=%s job_type=%s repository_id=%s attempt=%s/%s "
                "worker_id=%s error_type=%s error=%s payload=%s",
                job.id,
                job.job_type.value,
                job.repository_id,
                job.attempt_count,
                job.max_attempts,
                self.worker_id,
                type(exc).__name__,
                str(exc),
                job.payload,
            )
            failed_job = self.job_service.mark_failed(
                job.id,
                error_summary=str(exc),
                result_metadata=result_metadata,
            )
            if (
                failed_job.status == JobStatus.FAILED
                and failed_job.job_type == JobType.INGEST_REPOSITORY
                and failed_job.repository_id is not None
            ):
                self.repository_service.mark_repository_failed(failed_job.repository_id)
            return True

        self.job_service.mark_succeeded(job.id, result_metadata=result_metadata)
        logger.info(
            "job succeeded job_id=%s job_type=%s repository_id=%s worker_id=%s result_metadata=%s",
            job.id,
            job.job_type.value,
            job.repository_id,
            self.worker_id,
            result_metadata,
        )
        return True

    def dispatch(self, job: JobRead) -> JobResultMetadata:
        logger.info(
            "dispatching job job_id=%s job_type=%s repository_id=%s worker_id=%s",
            job.id,
            job.job_type.value,
            job.repository_id,
            self.worker_id,
        )
        if job.job_type == JobType.INGEST_REPOSITORY:
            return self.ingest_repository(job)
        if job.job_type == JobType.GENERATE_TEST:
            return self.generate_tests(job)
        if job.job_type == JobType.JUDGE_ANSWER:
            return self.judge_answer(job)

        raise ValueError(f"unsupported job type: {job.job_type.value}")

    def ingest_repository(self, job: JobRead) -> JobResultMetadata:
        logger.info(
            "calling repository_service.ingest_repository_job job_id=%s repository_id=%s payload=%s",
            job.id,
            job.repository_id,
            job.payload,
        )
        result = self.repository_service.ingest_repository_job(job)
        result_metadata: JobResultMetadata = {
            "repository_id": str(result.repository.id),
            "snapshot_id": str(result.snapshot.id),
            "commit_sha": result.commit_sha,
            "file_count": result.file_count,
            "guidance_source_count": len(result.guidance_sources),
            "drift_recorded": result.drift_recorded,
            "drift_score": result.drift_score,
            "drift_severity": result.drift_severity,
        }
        logger.info(
            "repository indexed job_id=%s result_metadata=%s",
            job.id,
            result_metadata,
        )
        return result_metadata

    def generate_tests(self, job: JobRead) -> JobResultMetadata:
        logger.info(
            "calling test_generation_service.run_generation_job job_id=%s repository_id=%s payload=%s",
            job.id,
            job.repository_id,
            job.payload,
        )
        generation_result = self.test_generation_service.run_generation_job(job)
        run_result = generation_result.run_result
        result_metadata: JobResultMetadata = {
            "repository_id": str(job.repository_id) if job.repository_id is not None else None,
            "agent_run_id": str(run_result.agent_run.id),
            "agent_run_status": run_result.agent_run.status.value,
            "generated_test_count": len(run_result.generated_tests),
            "generated_test_ids": [str(generated_test.id) for generated_test in run_result.generated_tests],
            "context_pack_ids": [
                str(context_pack.context_pack.id)
                for context_pack in generation_result.selected_context_packs
            ],
            "context_pack_types": [
                context_pack.context_pack.pack_type.value
                for context_pack in generation_result.selected_context_packs
            ],
        }
        logger.info(
            "generated tests job_id=%s result_metadata=%s",
            job.id,
            result_metadata,
        )
        return result_metadata

    def judge_answer(self, job: JobRead) -> JobResultMetadata:
        logger.info(
            "calling answer_judging_service.run_judge_answer_job job_id=%s repository_id=%s payload=%s",
            job.id,
            job.repository_id,
            job.payload,
        )
        generated_test_id = self._payload_uuid(job, "generated_test_id")
        test_answer_id = self._payload_uuid(job, "test_answer_id")
        run_result = self.answer_judging_service.run_judge_answer_job(job)
        result_metadata: JobResultMetadata = {
            "repository_id": str(job.repository_id) if job.repository_id is not None else None,
            "generated_test_id": str(generated_test_id),
            "test_answer_id": str(test_answer_id),
            "agent_run_id": str(run_result.agent_run.id),
            "test_result_id": str(run_result.test_result.id),
            "score": str(run_result.test_result.score),
            "status": run_result.test_result.status.value,
        }
        logger.info(
            "judged answer job_id=%s result_metadata=%s",
            job.id,
            result_metadata,
        )
        return result_metadata

    def _failure_metadata(self, job: JobRead, exc: Exception) -> JobResultMetadata:
        return {
            "repository_id": str(job.repository_id) if job.repository_id is not None else None,
            "job_type": job.job_type.value,
            "attempt_count": job.attempt_count,
            "max_attempts": job.max_attempts,
            "worker_id": self.worker_id,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }

    @staticmethod
    def _requested_pack_types(job: JobRead) -> set[ContextPackType] | None:
        return requested_pack_types_from_job(job)

    @staticmethod
    def _payload_uuid(job: JobRead, key: str) -> UUID:
        return payload_uuid(job, key)
