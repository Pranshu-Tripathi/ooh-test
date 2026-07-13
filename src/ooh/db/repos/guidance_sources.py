from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from ooh.db.connection import Database
from ooh.db.models import GuidanceSource, GuidanceSourceRead, GuidanceSourceType


class GuidanceSourceRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def replace_for_repository(
        self,
        repository_id: UUID,
        sources: list[tuple[GuidanceSourceType, str, str]],
    ) -> list[GuidanceSourceRead]:
        now = datetime.now(UTC)

        with self.db.session() as session:
            existing = {
                source.path: source
                for source in session.scalars(
                    select(GuidanceSource).where(GuidanceSource.repository_id == repository_id)
                )
            }
            active_paths = {path for _, path, _ in sources}
            refreshed: list[GuidanceSource] = []

            for source_type, path, content_hash in sources:
                source = existing.get(path)
                if source is None:
                    source = GuidanceSource(
                        repository_id=repository_id,
                        source_type=source_type,
                        path=path,
                        content_hash=content_hash,
                        enabled=True,
                        last_indexed_at=now,
                    )
                    session.add(source)
                else:
                    source.source_type = source_type
                    source.content_hash = content_hash
                    source.enabled = True
                    source.last_indexed_at = now
                    source.updated_at = now
                refreshed.append(source)

            for path, source in existing.items():
                if path not in active_paths:
                    source.enabled = False
                    source.updated_at = now

            session.flush()
            for source in refreshed:
                session.refresh(source)
            return [GuidanceSourceRead.model_validate(source) for source in refreshed]

    def list_enabled_for_repository(self, repository_id: UUID) -> list[GuidanceSourceRead]:
        with self.db.session() as session:
            sources = session.scalars(
                select(GuidanceSource)
                .where(
                    GuidanceSource.repository_id == repository_id,
                    GuidanceSource.enabled.is_(True),
                )
                .order_by(GuidanceSource.path)
            ).all()
            return [GuidanceSourceRead.model_validate(source) for source in sources]
