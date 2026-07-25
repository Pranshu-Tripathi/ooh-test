from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select

from ooh.db.connection import Database
from ooh.db.models import DriftEvent, DriftEventRead, DriftSeverity


class DriftEventRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(
        self,
        *,
        repository_id: UUID,
        snapshot_id: UUID | None,
        from_commit_sha: str | None,
        to_commit_sha: str,
        drift_score: Decimal,
        severity: DriftSeverity,
        breakdown: dict[str, Any],
    ) -> DriftEventRead:
        with self.db.session() as session:
            drift_event = DriftEvent(
                repository_id=repository_id,
                snapshot_id=snapshot_id,
                from_commit_sha=from_commit_sha,
                to_commit_sha=to_commit_sha,
                drift_score=drift_score,
                severity=severity,
                breakdown=breakdown,
            )
            session.add(drift_event)
            session.flush()
            session.refresh(drift_event)
            return DriftEventRead.model_validate(drift_event)

    def get(self, drift_event_id: UUID) -> DriftEventRead | None:
        with self.db.session() as session:
            drift_event = session.get(DriftEvent, drift_event_id)
            if drift_event is None:
                return None
            return DriftEventRead.model_validate(drift_event)

    def list_for_repository(self, repository_id: UUID, *, limit: int = 50) -> list[DriftEventRead]:
        with self.db.session() as session:
            drift_events = session.scalars(
                select(DriftEvent)
                .where(DriftEvent.repository_id == repository_id)
                .order_by(DriftEvent.created_at.desc())
                .limit(limit)
            ).all()
            return [DriftEventRead.model_validate(drift_event) for drift_event in drift_events]

    def latest_for_repository(self, repository_id: UUID) -> DriftEventRead | None:
        with self.db.session() as session:
            drift_event = session.scalar(
                select(DriftEvent)
                .where(DriftEvent.repository_id == repository_id)
                .order_by(DriftEvent.created_at.desc())
                .limit(1)
            )
            if drift_event is None:
                return None
            return DriftEventRead.model_validate(drift_event)
