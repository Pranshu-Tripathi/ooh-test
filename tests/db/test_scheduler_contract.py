from ooh.db.models import DriftTriggerEvaluation, Job, RepositorySchedule


def test_scheduler_tables_encode_durable_idempotent_evaluation() -> None:
    schedule_table = RepositorySchedule.__table__
    evaluation_table = DriftTriggerEvaluation.__table__

    assert {
        "enabled",
        "drift_min_score",
        "drift_max_score",
        "pack_types",
        "active_since",
    } <= set(schedule_table.columns.keys())
    assert {constraint.name for constraint in schedule_table.constraints} >= {
        "ck_repository_schedules_min_score",
        "ck_repository_schedules_score_range",
        "uq_repository_schedules_repository",
    }
    assert {constraint.name for constraint in evaluation_table.constraints} >= {
        "uq_drift_trigger_evaluations_schedule_event"
    }


def test_jobs_expose_lease_and_idempotency_contract() -> None:
    table = Job.__table__

    assert {"lease_expires_at", "idempotency_key"} <= set(table.columns.keys())
    assert {constraint.name for constraint in table.constraints} >= {
        "ck_jobs_attempt_count_non_negative",
        "ck_jobs_max_attempts_positive",
        "uq_jobs_idempotency_key",
    }
    assert "ix_jobs_lease_expiry" in {index.name for index in table.indexes}
