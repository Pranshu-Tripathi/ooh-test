from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ooh.db.connection import Database
from ooh.db.models import (
    AttentionFocusArea,
    AttentionFocusAreaRead,
    AttentionProfile,
    AttentionProfileRead,
)


@dataclass(frozen=True)
class AttentionFocusAreaInput:
    name: str
    description: str | None
    weight: Decimal
    path_globs: list[str]


@dataclass(frozen=True)
class AttentionProfileWithFocusAreas:
    profile: AttentionProfileRead
    focus_areas: list[AttentionFocusAreaRead]


class AttentionProfileRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(
        self,
        *,
        repository_id: UUID,
        name: str,
        default_weight: Decimal,
        active: bool,
        focus_areas: list[AttentionFocusAreaInput],
    ) -> AttentionProfileWithFocusAreas:
        with self.db.session() as session:
            if active:
                self._deactivate_repository_profiles(session, repository_id)
                session.flush()

            profile = AttentionProfile(
                repository_id=repository_id,
                name=name,
                default_weight=default_weight,
                active=active,
            )
            session.add(profile)
            session.flush()

            created_focus_areas: list[AttentionFocusArea] = []
            for focus_area_input in focus_areas:
                focus_area = AttentionFocusArea(
                    attention_profile_id=profile.id,
                    name=focus_area_input.name,
                    description=focus_area_input.description,
                    weight=focus_area_input.weight,
                    path_globs=focus_area_input.path_globs,
                )
                session.add(focus_area)
                created_focus_areas.append(focus_area)

            session.flush()
            session.refresh(profile)
            for focus_area in created_focus_areas:
                session.refresh(focus_area)

            return AttentionProfileWithFocusAreas(
                profile=AttentionProfileRead.model_validate(profile),
                focus_areas=[
                    AttentionFocusAreaRead.model_validate(focus_area)
                    for focus_area in created_focus_areas
                ],
            )

    def list_for_repository(self, repository_id: UUID) -> list[AttentionProfileWithFocusAreas]:
        with self.db.session() as session:
            profiles = session.scalars(
                select(AttentionProfile)
                .where(AttentionProfile.repository_id == repository_id)
                .order_by(AttentionProfile.created_at.desc())
            ).all()

            if not profiles:
                return []

            profile_ids = [profile.id for profile in profiles]
            focus_areas_by_profile_id: dict[UUID, list[AttentionFocusArea]] = {
                profile_id: [] for profile_id in profile_ids
            }
            for focus_area in session.scalars(
                select(AttentionFocusArea)
                .where(AttentionFocusArea.attention_profile_id.in_(profile_ids))
                .order_by(AttentionFocusArea.created_at)
            ):
                focus_areas_by_profile_id[focus_area.attention_profile_id].append(focus_area)

            return [
                AttentionProfileWithFocusAreas(
                    profile=AttentionProfileRead.model_validate(profile),
                    focus_areas=[
                        AttentionFocusAreaRead.model_validate(focus_area)
                        for focus_area in focus_areas_by_profile_id[profile.id]
                    ],
                )
                for profile in profiles
            ]

    def get_active_for_repository(self, repository_id: UUID) -> AttentionProfileWithFocusAreas | None:
        with self.db.session() as session:
            profile = session.scalar(
                select(AttentionProfile)
                .where(
                    AttentionProfile.repository_id == repository_id,
                    AttentionProfile.active.is_(True),
                )
                .limit(1)
            )
            if profile is None:
                return None

            focus_areas = session.scalars(
                select(AttentionFocusArea)
                .where(AttentionFocusArea.attention_profile_id == profile.id)
                .order_by(AttentionFocusArea.created_at)
            ).all()
            return AttentionProfileWithFocusAreas(
                profile=AttentionProfileRead.model_validate(profile),
                focus_areas=[
                    AttentionFocusAreaRead.model_validate(focus_area) for focus_area in focus_areas
                ],
            )

    def activate(self, *, repository_id: UUID, profile_id: UUID) -> AttentionProfileWithFocusAreas:
        with self.db.session() as session:
            profile = session.scalar(
                select(AttentionProfile).where(
                    AttentionProfile.id == profile_id,
                    AttentionProfile.repository_id == repository_id,
                )
            )
            if profile is None:
                raise ValueError(f"attention profile not found: {profile_id}")

            self._deactivate_repository_profiles(session, repository_id)
            profile.active = True
            session.flush()
            session.refresh(profile)

            focus_areas = session.scalars(
                select(AttentionFocusArea)
                .where(AttentionFocusArea.attention_profile_id == profile.id)
                .order_by(AttentionFocusArea.created_at)
            ).all()
            return AttentionProfileWithFocusAreas(
                profile=AttentionProfileRead.model_validate(profile),
                focus_areas=[
                    AttentionFocusAreaRead.model_validate(focus_area) for focus_area in focus_areas
                ],
            )

    @staticmethod
    def _deactivate_repository_profiles(session: Session, repository_id: UUID) -> None:
        profiles = session.scalars(
            select(AttentionProfile).where(AttentionProfile.repository_id == repository_id)
        )
        for profile in profiles:
            profile.active = False
