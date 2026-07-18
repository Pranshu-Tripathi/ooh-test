"""add live execution contract

Revision ID: 20260719_0003
Revises: 20260715_0002
Create Date: 2026-07-19 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260719_0003"
down_revision: str | None = "20260715_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_steps",
        sa.Column("parent_step_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "agent_steps",
        sa.Column("context_pack_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("agent_steps", sa.Column("activity", sa.Text(), nullable=True))
    op.add_column("agent_steps", sa.Column("iteration", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_agent_steps_parent_step_id_agent_steps",
        "agent_steps",
        "agent_steps",
        ["parent_step_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_agent_steps_context_pack_id_context_packs",
        "agent_steps",
        "context_packs",
        ["context_pack_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_agent_steps_parent", "agent_steps", ["parent_step_id"])
    op.create_index("ix_agent_steps_context_pack", "agent_steps", ["context_pack_id"])
    op.create_check_constraint(
        "ck_agent_steps_iteration_positive",
        "agent_steps",
        "iteration is null or iteration > 0",
    )
    op.create_check_constraint(
        "ck_agent_steps_not_own_parent",
        "agent_steps",
        "parent_step_id is null or parent_step_id <> id",
    )

    op.create_table(
        "execution_event_cursors",
        sa.Column(
            "singleton_id",
            sa.SmallInteger(),
            primary_key=True,
            autoincrement=False,
        ),
        sa.Column("last_event_id", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "singleton_id = 1",
            name="ck_execution_event_cursors_singleton",
        ),
    )
    op.execute(
        sa.text("insert into execution_event_cursors (singleton_id, last_event_id) values (1, 0)")
    )

    op.create_table(
        "execution_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column(
            "repository_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "agent_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "agent_step_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_steps.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column(
            "payload",
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
    )
    op.create_index(
        "ix_execution_events_repository_id",
        "execution_events",
        ["repository_id", "id"],
    )
    op.create_index(
        "ix_execution_events_job_id",
        "execution_events",
        ["job_id", "id"],
    )
    op.create_index(
        "ix_execution_events_agent_run_id",
        "execution_events",
        ["agent_run_id", "id"],
    )
    op.create_index(
        "ix_execution_events_agent_step_id",
        "execution_events",
        ["agent_step_id", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_execution_events_agent_step_id", table_name="execution_events")
    op.drop_index("ix_execution_events_agent_run_id", table_name="execution_events")
    op.drop_index("ix_execution_events_job_id", table_name="execution_events")
    op.drop_index("ix_execution_events_repository_id", table_name="execution_events")
    op.drop_table("execution_events")
    op.drop_table("execution_event_cursors")

    op.drop_constraint("ck_agent_steps_not_own_parent", "agent_steps", type_="check")
    op.drop_constraint("ck_agent_steps_iteration_positive", "agent_steps", type_="check")
    op.drop_index("ix_agent_steps_context_pack", table_name="agent_steps")
    op.drop_index("ix_agent_steps_parent", table_name="agent_steps")
    op.drop_constraint(
        "fk_agent_steps_context_pack_id_context_packs",
        "agent_steps",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_agent_steps_parent_step_id_agent_steps",
        "agent_steps",
        type_="foreignkey",
    )
    op.drop_column("agent_steps", "iteration")
    op.drop_column("agent_steps", "activity")
    op.drop_column("agent_steps", "context_pack_id")
    op.drop_column("agent_steps", "parent_step_id")
