from ooh.api.main import runtime, settings
from ooh.generation_config import (
    MAX_GENERATION_QUESTIONS_PER_CATEGORY,
    MAX_GENERATION_QUESTIONS_PER_JOB,
)


def test_runtime_exposes_generation_defaults_and_limits() -> None:
    payload = runtime()

    assert payload["generation_questions_per_category"] == (
        settings.generation_questions_per_category
    )
    assert payload["generation_max_questions_per_category"] == (
        MAX_GENERATION_QUESTIONS_PER_CATEGORY
    )
    assert payload["generation_max_questions_per_job"] == (
        MAX_GENERATION_QUESTIONS_PER_JOB
    )
