from decimal import Decimal
from uuid import UUID

from sqlalchemy import exists, func, select

from ooh.db.connection import Database
from ooh.db.models import (
    ContextPackType,
    DriftEvent,
    DriftTriggerEvaluation,
    DriftTriggerEvaluationRead,
    ExecutionEventType,
    JobType,
    RepositorySchedule,
    RepositoryScheduleRead,
)
from ooh.db.repos.execution_events import ExecutionEventInput, append_execution_event
from ooh.db.repos.jobs import enqueue_job


class RepositoryScheduleRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get_for_repository(self, repository_id: UUID) -> RepositoryScheduleRead | None:
        with self.db.session() as session:
            schedule = session.scalar(
                select(RepositorySchedule).where(
                    RepositorySchedule.repository_id == repository_id
                )
            )
            if schedule is None:
                return None
            return RepositoryScheduleRead.model_validate(schedule)

    def upsert(
        self,
        *,
        repository_id: UUID,
        enabled: bool,
        drift_min_score: Decimal,
        drift_max_score: Decimal | None,
        pack_types: list[ContextPackType],
    ) -> RepositoryScheduleRead:
        with self.db.session() as session:
            schedule = session.scalar(
                select(RepositorySchedule)
                .where(RepositorySchedule.repository_id == repository_id)
                .with_for_update()
            )
            if schedule is None:
                schedule = RepositorySchedule(
                    repository_id=repository_id,
                    enabled=enabled,
                    drift_min_score=drift_min_score,
                    drift_max_score=drift_max_score,
                    pack_types=[pack_type.value for pack_type in pack_types],
                )
                session.add(schedule)
            else:
                if enabled and not schedule.enabled:
                    # Enabling starts from this point so time spent disabled cannot replay.
                    schedule.active_since = func.now()
                schedule.enabled = enabled
                schedule.drift_min_score = drift_min_score
                schedule.drift_max_score = drift_max_score
                schedule.pack_types = [pack_type.value for pack_type in pack_types]
                schedule.updated_at = func.now()
            session.flush()
            session.refresh(schedule)
            append_execution_event(
                session,
                ExecutionEventInput(
                    event_type=ExecutionEventType.SCHEDULE_UPDATED,
                    repository_id=repository_id,
                    payload={
                        "schedule_id": str(schedule.id),
                        "enabled": schedule.enabled,
                        "drift_min_score": str(schedule.drift_min_score),
                        "drift_max_score": (
                            str(schedule.drift_max_score)
                            if schedule.drift_max_score is not None
                            else None
                        ),
                        "pack_types": schedule.pack_types,
                        "active_since": schedule.active_since.isoformat(),
                    },
                ),
            )
            return RepositoryScheduleRead.model_validate(schedule)

    def evaluate_pending(self, *, limit: int) -> list[DriftTriggerEvaluationRead]:
        if limit < 1:
            raise ValueError("scheduler batch limit must be positive")

        with self.db.session() as session:
            already_evaluated = exists(
                select(DriftTriggerEvaluation.id).where(
                    DriftTriggerEvaluation.repository_schedule_id == RepositorySchedule.id,
                    DriftTriggerEvaluation.drift_event_id == DriftEvent.id,
                )
            )
            candidates = session.execute(
                select(RepositorySchedule, DriftEvent)
                .join(
                    DriftEvent,
                    DriftEvent.repository_id == RepositorySchedule.repository_id,
                )
                .where(
                    RepositorySchedule.enabled.is_(True),
                    DriftEvent.created_at >= RepositorySchedule.active_since,
                    DriftEvent.from_commit_sha.is_not(None),
                    DriftEvent.snapshot_id.is_not(None),
                    ~already_evaluated,
                )
                .order_by(DriftEvent.created_at, DriftEvent.id)
                .with_for_update(of=RepositorySchedule, skip_locked=True)
                .limit(limit)
            ).all()

            evaluations: list[DriftTriggerEvaluationRead] = []
            for schedule, drift_event in candidates:
                matched = self.score_matches(
                    drift_event.drift_score,
                    minimum=schedule.drift_min_score,
                    maximum=schedule.drift_max_score,
                )
                job = None
                if matched:
                    job = enqueue_job(
                        session,
                        repository_id=schedule.repository_id,
                        job_type=JobType.GENERATE_TEST,
                        idempotency_key=f"drift-trigger:{schedule.id}:{drift_event.id}",
                        payload={
                            "repository_id": str(schedule.repository_id),
                            "snapshot_id": str(drift_event.snapshot_id),
                            "drift_event_id": str(drift_event.id),
                            "pack_types": schedule.pack_types,
                            "trigger": {
                                "type": "drift_score_range",
                                "repository_schedule_id": str(schedule.id),
                                "drift_min_score": str(schedule.drift_min_score),
                                "drift_max_score": (
                                    str(schedule.drift_max_score)
                                    if schedule.drift_max_score is not None
                                    else None
                                ),
                            },
                        },
                    )

                evaluation_payload = {
                    "drift_score": str(drift_event.drift_score),
                    "drift_min_score": str(schedule.drift_min_score),
                    "drift_max_score": (
                        str(schedule.drift_max_score)
                        if schedule.drift_max_score is not None
                        else None
                    ),
                    "pack_types": schedule.pack_types,
                }
                evaluation = DriftTriggerEvaluation(
                    repository_schedule_id=schedule.id,
                    drift_event_id=drift_event.id,
                    matched=matched,
                    job_id=job.id if job is not None else None,
                    evaluation=evaluation_payload,
                )
                session.add(evaluation)
                session.flush()
                session.refresh(evaluation)
                append_execution_event(
                    session,
                    ExecutionEventInput(
                        event_type=ExecutionEventType.DRIFT_TRIGGER_EVALUATED,
                        repository_id=schedule.repository_id,
                        job_id=job.id if job is not None else None,
                        payload={
                            "schedule_id": str(schedule.id),
                            "drift_event_id": str(drift_event.id),
                            "matched": matched,
                            **evaluation_payload,
                        },
                    ),
                )
                evaluations.append(DriftTriggerEvaluationRead.model_validate(evaluation))
            return evaluations

    @staticmethod
    def score_matches(
        score: Decimal,
        *,
        minimum: Decimal,
        maximum: Decimal | None,
    ) -> bool:
        return score >= minimum and (maximum is None or score <= maximum)
