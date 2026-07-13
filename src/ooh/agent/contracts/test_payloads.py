from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator


class StrictPayloadModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceRef(StrictPayloadModel):
    source_type: str = Field(min_length=1, max_length=100)
    source_uri: str = Field(min_length=1, max_length=2000)
    content_hash: str | None = Field(default=None, max_length=200)
    context_pack_id: str | None = Field(default=None, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RubricCriterion(StrictPayloadModel):
    criterion: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=1000)
    weight: float = Field(gt=0, le=1)


class McqOption(StrictPayloadModel):
    id: str = Field(min_length=1, max_length=20)
    text: str = Field(min_length=1, max_length=1000)


class BaseGeneratedTestPayload(StrictPayloadModel):
    schema_version: int = Field(default=1, ge=1)
    question: str = Field(min_length=1, max_length=4000)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list, max_length=100)
    rubric: list[RubricCriterion] = Field(default_factory=list, max_length=20)
    explanation: str | None = Field(default=None, max_length=4000)

    @field_validator("question")
    @classmethod
    def question_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("question must not be blank")
        return stripped


class ShortAnswerTestPayload(BaseGeneratedTestPayload):
    type: Literal["short_answer"]
    expected_answer: str = Field(min_length=1, max_length=8000)

    @field_validator("expected_answer")
    @classmethod
    def expected_answer_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("expected_answer must not be blank")
        return stripped


class McqSingleTestPayload(BaseGeneratedTestPayload):
    type: Literal["mcq_single"]
    options: list[McqOption] = Field(min_length=2, max_length=10)
    correct_option_ids: list[str] = Field(min_length=1, max_length=1)

    @model_validator(mode="after")
    def correct_option_must_match_options(self) -> "McqSingleTestPayload":
        validate_option_ids(self.options, self.correct_option_ids)
        return self


class McqMultiTestPayload(BaseGeneratedTestPayload):
    type: Literal["mcq_multi"]
    options: list[McqOption] = Field(min_length=2, max_length=12)
    correct_option_ids: list[str] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def correct_options_must_match_options(self) -> "McqMultiTestPayload":
        validate_option_ids(self.options, self.correct_option_ids)
        return self


GeneratedTestPayload = Annotated[
    ShortAnswerTestPayload | McqSingleTestPayload | McqMultiTestPayload,
    Field(discriminator="type"),
]

_generated_test_payload_adapter = TypeAdapter(GeneratedTestPayload)


def validate_generated_test_payload(payload: dict[str, Any]) -> GeneratedTestPayload:
    return _generated_test_payload_adapter.validate_python(payload)


def normalize_generated_test_payload(payload: dict[str, Any]) -> dict[str, Any]:
    validated_payload = validate_generated_test_payload(payload)
    return validated_payload.model_dump(mode="json", exclude_none=True)


def validate_option_ids(options: list[McqOption], correct_option_ids: list[str]) -> None:
    option_ids = [option.id for option in options]
    duplicate_option_ids = {option_id for option_id in option_ids if option_ids.count(option_id) > 1}
    if duplicate_option_ids:
        raise ValueError(f"duplicate option ids: {sorted(duplicate_option_ids)}")

    duplicate_correct_ids = {
        option_id for option_id in correct_option_ids if correct_option_ids.count(option_id) > 1
    }
    if duplicate_correct_ids:
        raise ValueError(f"duplicate correct option ids: {sorted(duplicate_correct_ids)}")

    unknown_ids = sorted(set(correct_option_ids) - set(option_ids))
    if unknown_ids:
        raise ValueError(f"correct option ids are not present in options: {unknown_ids}")
