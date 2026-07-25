import pytest
from pydantic import ValidationError

from ooh.config import Settings
from ooh.generation_config import DEFAULT_GENERATION_QUESTIONS_PER_CATEGORY


def test_generation_questions_per_category_has_a_safe_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OOH_GENERATION_QUESTIONS_PER_CATEGORY", raising=False)

    settings = Settings(_env_file=None)

    assert (
        settings.generation_questions_per_category
        == DEFAULT_GENERATION_QUESTIONS_PER_CATEGORY
    )


def test_generation_questions_per_category_uses_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OOH_GENERATION_QUESTIONS_PER_CATEGORY", "2")

    settings = Settings(_env_file=None)

    assert settings.generation_questions_per_category == 2


def test_generation_questions_per_category_rejects_an_unsafe_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OOH_GENERATION_QUESTIONS_PER_CATEGORY", "4")

    with pytest.raises(ValidationError, match="less than or equal to 3"):
        Settings(_env_file=None)
