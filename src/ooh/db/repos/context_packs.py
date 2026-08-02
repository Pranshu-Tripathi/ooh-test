from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from ooh.db.connection import Database
from ooh.db.models import (
    ContextPack,
    ContextPackRead,
    ContextPackSource,
    ContextPackSourceRead,
    ContextPackSourceType,
    ContextPackType,
)


@dataclass(frozen=True)
class ContextPackSourceInput:
    source_type: ContextPackSourceType
    source_uri: str
    content_hash: str | None = None


@dataclass(frozen=True)
class ContextPackWithSources:
    context_pack: ContextPackRead
    sources: list[ContextPackSourceRead]


class ContextPackRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(
        self,
        *,
        repository_id: UUID,
        snapshot_id: UUID,
        attention_profile_id: UUID | None,
        pack_type: ContextPackType,
        artifact_uri: str,
        content_hash: str,
        sources: list[ContextPackSourceInput],
    ) -> ContextPackWithSources:
        with self.db.session() as session:
            context_pack = ContextPack(
                repository_id=repository_id,
                snapshot_id=snapshot_id,
                attention_profile_id=attention_profile_id,
                pack_type=pack_type,
                artifact_uri=artifact_uri,
                content_hash=content_hash,
            )
            session.add(context_pack)
            session.flush()

            created_sources: list[ContextPackSource] = []
            for source_input in sources:
                source = ContextPackSource(
                    context_pack_id=context_pack.id,
                    source_type=source_input.source_type,
                    source_uri=source_input.source_uri,
                    content_hash=source_input.content_hash,
                )
                session.add(source)
                created_sources.append(source)

            session.flush()
            session.refresh(context_pack)
            for source in created_sources:
                session.refresh(source)

            return ContextPackWithSources(
                context_pack=ContextPackRead.model_validate(context_pack),
                sources=[ContextPackSourceRead.model_validate(source) for source in created_sources],
            )

    def list_for_repository(
        self,
        repository_id: UUID,
        *,
        limit: int = 50,
    ) -> list[ContextPackWithSources]:
        with self.db.session() as session:
            context_packs = session.scalars(
                select(ContextPack)
                .where(ContextPack.repository_id == repository_id)
                .order_by(ContextPack.created_at.desc())
                .limit(limit)
            ).all()
            if not context_packs:
                return []

            context_pack_ids = [context_pack.id for context_pack in context_packs]
            sources_by_pack_id: dict[UUID, list[ContextPackSource]] = {
                context_pack_id: [] for context_pack_id in context_pack_ids
            }
            for source in session.scalars(
                select(ContextPackSource)
                .where(ContextPackSource.context_pack_id.in_(context_pack_ids))
                .order_by(ContextPackSource.created_at)
            ):
                sources_by_pack_id[source.context_pack_id].append(source)

            return [
                ContextPackWithSources(
                    context_pack=ContextPackRead.model_validate(context_pack),
                    sources=[
                        ContextPackSourceRead.model_validate(source)
                        for source in sources_by_pack_id[context_pack.id]
                    ],
                )
                for context_pack in context_packs
            ]
