import logging
from uuid import UUID

from ooh.config import get_settings
from ooh.db import Database
from ooh.db.models import ContextPackType, JobRead, JobStatus, JobType
from ooh.services import build_worker_services
from ooh.services.answer_judging import payload_uuid
from ooh.services.test_generation import requested_pack_types_from_job

logger = logging.getLogger(__name__)


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
            "claimed job",
            extra={"job_id": str(job.id), "job_type": job.job_type.value, "worker_id": self.worker_id},
        )

        try:
            self.dispatch(job)
        except Exception as exc:
            logger.exception("job failed", extra={"job_id": str(job.id), "job_type": job.job_type.value})
            failed_job = self.job_service.mark_failed(job.id, error_summary=str(exc))
            if (
                failed_job.status == JobStatus.FAILED
                and failed_job.job_type == JobType.INGEST_REPOSITORY
                and failed_job.repository_id is not None
            ):
                self.repository_service.mark_repository_failed(failed_job.repository_id)
            return True

        self.job_service.mark_succeeded(job.id)
        logger.info("job succeeded", extra={"job_id": str(job.id), "job_type": job.job_type.value})
        return True

    def dispatch(self, job: JobRead) -> None:
        if job.job_type == JobType.INGEST_REPOSITORY:
            self.ingest_repository(job)
            return
        if job.job_type == JobType.GENERATE_TEST:
            self.generate_tests(job)
            return
        if job.job_type == JobType.JUDGE_ANSWER:
            self.judge_answer(job)
            return

        raise ValueError(f"unsupported job type: {job.job_type.value}")

    def ingest_repository(self, job: JobRead) -> None:
        result = self.repository_service.ingest_repository_job(job)
        logger.info(
            "repository indexed",
            extra={
                "repository_id": str(result.repository.id),
                "commit_sha": result.commit_sha,
                "file_count": result.file_count,
                "guidance_source_count": len(result.guidance_sources),
                "drift_score": result.drift_score,
                "drift_severity": result.drift_severity,
                "drift_recorded": result.drift_recorded,
            },
        )

    def generate_tests(self, job: JobRead) -> None:
        generation_result = self.test_generation_service.run_generation_job(job)
        run_result = generation_result.run_result
        logger.info(
            "generated tests",
            extra={
                "repository_id": str(job.repository_id),
                "job_id": str(job.id),
                "agent_run_id": str(run_result.agent_run.id),
                "generated_test_count": len(run_result.generated_tests),
                "context_pack_ids": [
                    str(context_pack.context_pack.id)
                    for context_pack in generation_result.selected_context_packs
                ],
            },
        )

    def judge_answer(self, job: JobRead) -> None:
        generated_test_id = self._payload_uuid(job, "generated_test_id")
        test_answer_id = self._payload_uuid(job, "test_answer_id")
        run_result = self.answer_judging_service.run_judge_answer_job(job)
        logger.info(
            "judged answer",
            extra={
                "repository_id": str(job.repository_id),
                "job_id": str(job.id),
                "agent_run_id": str(run_result.agent_run.id),
                "generated_test_id": str(generated_test_id),
                "test_answer_id": str(test_answer_id),
                "test_result_id": str(run_result.test_result.id),
                "score": str(run_result.test_result.score),
                "status": run_result.test_result.status.value,
            },
        )

    @staticmethod
    def _requested_pack_types(job: JobRead) -> set[ContextPackType] | None:
        return requested_pack_types_from_job(job)

    @staticmethod
    def _payload_uuid(job: JobRead, key: str) -> UUID:
        return payload_uuid(job, key)
