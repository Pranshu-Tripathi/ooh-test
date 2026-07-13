from uuid import UUID

from sqlalchemy import select

from ooh.db.connection import Database
from ooh.db.models import RepoSnapshot, RepoSnapshotRead, RepoSnapshotStatus


class RepoSnapshotRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(
        self,
        *,
        repository_id: UUID,
        commit_sha: str,
        index_uri: str,
        status: RepoSnapshotStatus = RepoSnapshotStatus.COMPLETED,
    ) -> RepoSnapshotRead:
        with self.db.session() as session:
            snapshot = RepoSnapshot(
                repository_id=repository_id,
                commit_sha=commit_sha,
                index_uri=index_uri,
                status=status,
            )
            session.add(snapshot)
            session.flush()
            session.refresh(snapshot)
            return RepoSnapshotRead.model_validate(snapshot)

    def latest_for_repository(self, repository_id: UUID) -> RepoSnapshotRead | None:
        with self.db.session() as session:
            snapshot = session.scalar(
                select(RepoSnapshot)
                .where(RepoSnapshot.repository_id == repository_id)
                .order_by(RepoSnapshot.created_at.desc())
                .limit(1)
            )
            if snapshot is None:
                return None
            return RepoSnapshotRead.model_validate(snapshot)
