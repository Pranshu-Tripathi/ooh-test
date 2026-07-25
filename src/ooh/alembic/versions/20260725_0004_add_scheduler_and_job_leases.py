"""add scheduler policies and job leases

Revision ID: 20260725_0004
Revises: 20260719_0003
Create Date: 2026-07-25 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260725_0004"
down_revision: str | None = "20260719_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("lease_expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column("jobs", sa.Column("idempotency_key", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_jobs_attempt_count_non_negative",
        "jobs",
        "attempt_count >= 0",
    )
    op.create_check_constraint(
        "ck_jobs_max_attempts_positive",
        "jobs",
        "max_attempts > 0",
    )
    op.create_unique_constraint("uq_jobs_idempotency_key", "jobs", ["idempotency_key"])
    op.create_index("ix_jobs_lease_expiry", "jobs", ["status", "lease_expires_at"])

    op.create_table(
        "repository_schedules",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "repository_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("drift_min_score", sa.Numeric(10, 2), nullable=False, server_default="25"),
        sa.Column("drift_max_score", sa.Numeric(10, 2), nullable=True),
        sa.Column(
            "pack_types",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[\"low_level_components\"]'::jsonb"),
        ),
        sa.Column(
            "active_since",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "drift_min_score >= 0",
            name="ck_repository_schedules_min_score",
        ),
        sa.CheckConstraint(
            "drift_max_score is null or drift_max_score >= drift_min_score",
            name="ck_repository_schedules_score_range",
        ),
        sa.UniqueConstraint(
            "repository_id",
            name="uq_repository_schedules_repository",
        ),
    )
    op.create_index(
        "ix_repository_schedules_enabled",
        "repository_schedules",
        ["enabled", "active_since"],
    )

    op.create_table(
        "drift_trigger_evaluations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "repository_schedule_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("repository_schedules.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "drift_event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("drift_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("matched", sa.Boolean(), nullable=False),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "evaluation",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "repository_schedule_id",
            "drift_event_id",
            name="uq_drift_trigger_evaluations_schedule_event",
        ),
    )
    op.create_index(
        "ix_drift_trigger_evaluations_schedule_created",
        "drift_trigger_evaluations",
        ["repository_schedule_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_drift_trigger_evaluations_schedule_created",
        table_name="drift_trigger_evaluations",
    )
    op.drop_table("drift_trigger_evaluations")
    op.drop_index("ix_repository_schedules_enabled", table_name="repository_schedules")
    op.drop_table("repository_schedules")

    op.drop_index("ix_jobs_lease_expiry", table_name="jobs")
    op.drop_constraint("uq_jobs_idempotency_key", "jobs", type_="unique")
    op.drop_constraint("ck_jobs_max_attempts_positive", "jobs", type_="check")
    op.drop_constraint("ck_jobs_attempt_count_non_negative", "jobs", type_="check")
    op.drop_column("jobs", "idempotency_key")
    op.drop_column("jobs", "lease_expires_at")
