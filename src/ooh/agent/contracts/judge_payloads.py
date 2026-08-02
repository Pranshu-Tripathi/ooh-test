from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from ooh.agent.contracts.test_payloads import EvidenceRef


class StrictJudgeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SuggestedLearning(StrictJudgeModel):
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=2000)


class JudgeResultPayload(StrictJudgeModel):
    score: float = Field(ge=0, le=1)
    status: Literal["passing", "needs_review", "getting_out_of_hand"]
    missed_concepts: list[str] = Field(default_factory=list, max_length=20)
    feedback: str = Field(min_length=1, max_length=4000)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list, max_length=100)
    suggested_learning: SuggestedLearning | None = None

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("missed_concepts")
    @classmethod
    def missed_concepts_must_not_be_blank(cls, value: list[str]) -> list[str]:
        return [concept.strip() for concept in value if concept.strip()]

    @field_validator("feedback")
    @classmethod
    def feedback_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("feedback must not be blank")
        return stripped


_judge_result_payload_adapter = TypeAdapter(Annotated[JudgeResultPayload, Field()])


def validate_judge_result_payload(payload: dict[str, Any]) -> JudgeResultPayload:
    return _judge_result_payload_adapter.validate_python(payload)


def normalize_judge_result_payload(payload: dict[str, Any]) -> dict[str, Any]:
    validated_payload = validate_judge_result_payload(payload)
    return validated_payload.model_dump(mode="json", exclude_none=True)
