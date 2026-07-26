"""add repository schedule generation mix

Revision ID: 20260726_0005
Revises: 20260725_0004
Create Date: 2026-07-26 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260726_0005"
down_revision: str | None = "20260725_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "repository_schedules",
        sa.Column(
            "generation_plan",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text(
                """'[{"category": "low_level_components", "question_count": 1}]'::jsonb"""
            ),
        ),
    )
    op.execute(
        """
        UPDATE repository_schedules AS schedule
        SET generation_plan = COALESCE(
            (
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'category', category,
                        'question_count', 1
                    )
                )
                FROM jsonb_array_elements_text(schedule.pack_types)
                    AS scheduled_category(category)
            ),
            '[{"category": "low_level_components", "question_count": 1}]'::jsonb
        )
        """
    )
    op.add_column(
        "repository_schedules",
        sa.Column(
            "max_questions_per_trigger",
            sa.Integer(),
            nullable=False,
            server_default="15",
        ),
    )
    op.create_check_constraint(
        "ck_repository_schedules_question_limit",
        "repository_schedules",
        "max_questions_per_trigger > 0 and max_questions_per_trigger <= 15",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_repository_schedules_question_limit",
        "repository_schedules",
        type_="check",
    )
    op.drop_column("repository_schedules", "max_questions_per_trigger")
    op.drop_column("repository_schedules", "generation_plan")
