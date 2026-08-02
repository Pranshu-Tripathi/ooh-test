"""initial phase 1 schema

Revision ID: 20260712_0001
Revises:
Create Date: 2026-07-12 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260712_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def uuid_pk() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def created_at() -> sa.Column:
    return sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()"))


def updated_at() -> sa.Column:
    return sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()"))


def upgrade() -> None:
    op.execute('create extension if not exists "pgcrypto"')

    op.create_table(
        "repositories",
        uuid_pk(),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_uri", sa.Text(), nullable=False),
        sa.Column("default_branch", sa.Text(), nullable=True),
        sa.Column("token_ref", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("last_processed_commit_sha", sa.Text(), nullable=True),
        sa.Column("last_indexed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        created_at(),
        updated_at(),
        sa.CheckConstraint("source_type in ('local_path', 'github')", name="ck_repositories_source_type"),
    )
    op.create_index("ix_repositories_source_type", "repositories", ["source_type"])
    op.create_index("ix_repositories_status", "repositories", ["status"])

    op.create_table(
        "repo_snapshots",
        uuid_pk(),
        sa.Column(
            "repository_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("commit_sha", sa.Text(), nullable=False),
        sa.Column("index_uri", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="completed"),
        created_at(),
    )
    op.create_index("ix_repo_snapshots_repository_created", "repo_snapshots", ["repository_id", "created_at"])
    op.create_index("ix_repo_snapshots_commit_sha", "repo_snapshots", ["commit_sha"])

    op.create_table(
        "attention_profiles",
        uuid_pk(),
        sa.Column(
            "repository_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("default_weight", sa.Numeric(6, 2), nullable=False, server_default="1.0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        created_at(),
        sa.UniqueConstraint("repository_id", "name", name="uq_attention_profiles_repository_name"),
    )
    op.create_index(
        "uq_attention_profiles_one_active",
        "attention_profiles",
        ["repository_id"],
        unique=True,
        postgresql_where=sa.text("active"),
    )

    op.create_table(
        "attention_focus_areas",
        uuid_pk(),
        sa.Column(
            "attention_profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("attention_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("weight", sa.Numeric(6, 2), nullable=False, server_default="1.0"),
        sa.Column("path_globs", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        created_at(),
        sa.UniqueConstraint("attention_profile_id", "name", name="uq_attention_focus_areas_profile_name"),
    )

    op.create_table(
        "guidance_sources",
        uuid_pk(),
        sa.Column(
            "repository_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_indexed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        created_at(),
        updated_at(),
        sa.UniqueConstraint("repository_id", "path", name="uq_guidance_sources_repository_path"),
    )
    op.create_index("ix_guidance_sources_enabled", "guidance_sources", ["repository_id", "enabled"])

    op.create_table(
        "jobs",
        uuid_pk(),
        sa.Column(
            "repository_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("job_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("run_after", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("locked_by", sa.Text(), nullable=True),
        sa.Column("locked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        created_at(),
        updated_at(),
        sa.CheckConstraint(
            "status in ('queued', 'running', 'retry_wait', 'succeeded', 'failed', 'cancelled')",
            name="ck_jobs_status",
        ),
    )
    op.create_index("ix_jobs_claimable", "jobs", ["status", "run_after", "created_at"])
    op.create_index("ix_jobs_repository", "jobs", ["repository_id"])

    op.create_table(
        "drift_events",
        uuid_pk(),
        sa.Column(
            "repository_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("repo_snapshots.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("from_commit_sha", sa.Text(), nullable=True),
        sa.Column("to_commit_sha", sa.Text(), nullable=False),
        sa.Column("drift_score", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("severity", sa.Text(), nullable=False, server_default="low"),
        sa.Column("breakdown", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        created_at(),
    )
    op.create_index("ix_drift_events_repository_created", "drift_events", ["repository_id", "created_at"])

    op.create_table(
        "agent_runs",
        uuid_pk(),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "repository_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("run_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("model_profile", sa.Text(), nullable=True),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        created_at(),
    )
    op.create_index("ix_agent_runs_job", "agent_runs", ["job_id"])
    op.create_index("ix_agent_runs_repository_created", "agent_runs", ["repository_id", "created_at"])

    op.create_table(
        "agent_steps",
        uuid_pk(),
        sa.Column(
            "agent_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("input_summary", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("output_summary", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("warning_summary", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        created_at(),
        sa.UniqueConstraint("agent_run_id", "sequence", name="uq_agent_steps_run_sequence"),
    )
    op.create_index("ix_agent_steps_run", "agent_steps", ["agent_run_id", "sequence"])

    op.create_table(
        "agent_artifacts",
        uuid_pk(),
        sa.Column(
            "agent_step_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_steps.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("artifact_type", sa.Text(), nullable=False),
        sa.Column("artifact_uri", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=True),
        created_at(),
    )
    op.create_index("ix_agent_artifacts_step", "agent_artifacts", ["agent_step_id"])

    op.create_table(
        "provenance_refs",
        uuid_pk(),
        sa.Column(
            "artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_artifacts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ref_type", sa.Text(), nullable=False),
        sa.Column("ref_uri", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        created_at(),
    )
    op.create_index("ix_provenance_refs_artifact", "provenance_refs", ["artifact_id"])

    op.create_table(
        "context_packs",
        uuid_pk(),
        sa.Column(
            "repository_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("repo_snapshots.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "attention_profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("attention_profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("pack_type", sa.Text(), nullable=False),
        sa.Column("artifact_uri", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=True),
        created_at(),
    )
    op.create_index("ix_context_packs_snapshot", "context_packs", ["snapshot_id", "pack_type"])

    op.create_table(
        "context_pack_sources",
        uuid_pk(),
        sa.Column(
            "context_pack_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("context_packs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_uri", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=True),
        created_at(),
    )
    op.create_index("ix_context_pack_sources_pack", "context_pack_sources", ["context_pack_id"])

    op.create_table(
        "generated_tests",
        uuid_pk(),
        sa.Column(
            "repository_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("repo_snapshots.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "drift_event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("drift_events.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "agent_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "context_pack_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("context_packs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("test_payload", postgresql.JSONB(), nullable=False),
        sa.Column("evidence_refs", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("prompt_version", sa.Text(), nullable=True),
        created_at(),
    )
    op.create_index("ix_generated_tests_repository_created", "generated_tests", ["repository_id", "created_at"])
    op.create_index("ix_generated_tests_snapshot", "generated_tests", ["snapshot_id"])

    op.create_table(
        "test_answers",
        uuid_pk(),
        sa.Column(
            "generated_test_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("generated_tests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("answer_payload", postgresql.JSONB(), nullable=False),
        sa.Column("submitted_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_test_answers_generated_test", "test_answers", ["generated_test_id"])

    op.create_table(
        "test_results",
        uuid_pk(),
        sa.Column(
            "generated_test_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("generated_tests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "test_answer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("test_answers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "agent_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("score", sa.Numeric(5, 4), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("feedback", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("alert_flag", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        created_at(),
        sa.CheckConstraint("score >= 0 and score <= 1", name="ck_test_results_score_range"),
    )
    op.create_index("ix_test_results_generated_test", "test_results", ["generated_test_id"])
    op.create_index("ix_test_results_status", "test_results", ["status"])

    op.create_table(
        "saved_learnings",
        uuid_pk(),
        sa.Column(
            "repository_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "test_result_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("test_results.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("source_payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        created_at(),
    )
    op.create_index("ix_saved_learnings_repository_created", "saved_learnings", ["repository_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_saved_learnings_repository_created", table_name="saved_learnings")
    op.drop_table("saved_learnings")
    op.drop_index("ix_test_results_status", table_name="test_results")
    op.drop_index("ix_test_results_generated_test", table_name="test_results")
    op.drop_table("test_results")
    op.drop_index("ix_test_answers_generated_test", table_name="test_answers")
    op.drop_table("test_answers")
    op.drop_index("ix_generated_tests_snapshot", table_name="generated_tests")
    op.drop_index("ix_generated_tests_repository_created", table_name="generated_tests")
    op.drop_table("generated_tests")
    op.drop_index("ix_context_pack_sources_pack", table_name="context_pack_sources")
    op.drop_table("context_pack_sources")
    op.drop_index("ix_context_packs_snapshot", table_name="context_packs")
    op.drop_table("context_packs")
    op.drop_index("ix_provenance_refs_artifact", table_name="provenance_refs")
    op.drop_table("provenance_refs")
    op.drop_index("ix_agent_artifacts_step", table_name="agent_artifacts")
    op.drop_table("agent_artifacts")
    op.drop_index("ix_agent_steps_run", table_name="agent_steps")
    op.drop_table("agent_steps")
    op.drop_index("ix_agent_runs_repository_created", table_name="agent_runs")
    op.drop_index("ix_agent_runs_job", table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_index("ix_drift_events_repository_created", table_name="drift_events")
    op.drop_table("drift_events")
    op.drop_index("ix_jobs_repository", table_name="jobs")
    op.drop_index("ix_jobs_claimable", table_name="jobs")
    op.drop_table("jobs")
    op.drop_index("ix_guidance_sources_enabled", table_name="guidance_sources")
    op.drop_table("guidance_sources")
    op.drop_table("attention_focus_areas")
    op.drop_index("uq_attention_profiles_one_active", table_name="attention_profiles")
    op.drop_table("attention_profiles")
    op.drop_index("ix_repo_snapshots_commit_sha", table_name="repo_snapshots")
    op.drop_index("ix_repo_snapshots_repository_created", table_name="repo_snapshots")
    op.drop_table("repo_snapshots")
    op.drop_index("ix_repositories_status", table_name="repositories")
    op.drop_index("ix_repositories_source_type", table_name="repositories")
    op.drop_table("repositories")
    op.execute('drop extension if exists "pgcrypto"')
