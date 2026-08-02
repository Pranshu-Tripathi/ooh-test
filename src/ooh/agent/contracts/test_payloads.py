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
GeneratedTestType = Literal["short_answer", "mcq_single", "mcq_multi"]


class PublicEvidenceRef(StrictPayloadModel):
    source_type: str = Field(min_length=1, max_length=100)
    source_uri: str = Field(min_length=1, max_length=2000)
    content_hash: str | None = Field(default=None, max_length=200)
    context_pack_id: str | None = Field(default=None, max_length=100)


class BaseGeneratedTestPublicPayload(StrictPayloadModel):
    schema_version: int = Field(ge=1)
    question: str = Field(min_length=1, max_length=4000)
    evidence_refs: list[PublicEvidenceRef] = Field(default_factory=list, max_length=100)


class ShortAnswerPublicPayload(BaseGeneratedTestPublicPayload):
    type: Literal["short_answer"]


class McqSinglePublicPayload(BaseGeneratedTestPublicPayload):
    type: Literal["mcq_single"]
    options: list[McqOption] = Field(min_length=2, max_length=10)


class McqMultiPublicPayload(BaseGeneratedTestPublicPayload):
    type: Literal["mcq_multi"]
    options: list[McqOption] = Field(min_length=2, max_length=12)


GeneratedTestPublicPayload = Annotated[
    ShortAnswerPublicPayload | McqSinglePublicPayload | McqMultiPublicPayload,
    Field(discriminator="type"),
]


class BaseGeneratedTestGradingPayload(StrictPayloadModel):
    type: GeneratedTestType
    rubric: list[RubricCriterion] = Field(default_factory=list, max_length=20)
    explanation: str | None = Field(default=None, max_length=4000)


class ShortAnswerGradingPayload(BaseGeneratedTestGradingPayload):
    type: Literal["short_answer"]
    expected_answer: str = Field(min_length=1, max_length=8000)


class McqSingleGradingPayload(BaseGeneratedTestGradingPayload):
    type: Literal["mcq_single"]
    correct_option_ids: list[str] = Field(min_length=1, max_length=1)


class McqMultiGradingPayload(BaseGeneratedTestGradingPayload):
    type: Literal["mcq_multi"]
    correct_option_ids: list[str] = Field(min_length=1, max_length=12)


GeneratedTestGradingPayload = Annotated[
    ShortAnswerGradingPayload | McqSingleGradingPayload | McqMultiGradingPayload,
    Field(discriminator="type"),
]


class BaseSubmittedAnswerPayload(StrictPayloadModel):
    metadata: dict[str, Any] = Field(default_factory=dict)


class ShortAnswerSubmissionPayload(BaseSubmittedAnswerPayload):
    type: Literal["short_answer"]
    response_text: str = Field(min_length=1, max_length=8000)

    @field_validator("response_text")
    @classmethod
    def response_text_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("response_text must not be blank")
        return stripped


class McqSingleSubmissionPayload(BaseSubmittedAnswerPayload):
    type: Literal["mcq_single"]
    selected_option_id: str = Field(min_length=1, max_length=20)

    @field_validator("selected_option_id")
    @classmethod
    def selected_option_id_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("selected_option_id must not be blank")
        return stripped


class McqMultiSubmissionPayload(BaseSubmittedAnswerPayload):
    type: Literal["mcq_multi"]
    selected_option_ids: list[str] = Field(min_length=1, max_length=12)

    @field_validator("selected_option_ids")
    @classmethod
    def selected_option_ids_must_be_unique(cls, value: list[str]) -> list[str]:
        stripped = [option_id.strip() for option_id in value]
        if any(not option_id for option_id in stripped):
            raise ValueError("selected_option_ids must not contain blank values")
        if len(stripped) != len(set(stripped)):
            raise ValueError("selected_option_ids must be unique")
        return stripped


SubmittedAnswerPayload = Annotated[
    ShortAnswerSubmissionPayload | McqSingleSubmissionPayload | McqMultiSubmissionPayload,
    Field(discriminator="type"),
]


class _WireMcqOption(StrictPayloadModel):
    id: str
    text: str


class _BaseGeneratedTestWirePayload(StrictPayloadModel):
    question: str
    evidence_refs: list[str]
    explanation: str | None = None


class _ShortAnswerWirePayload(_BaseGeneratedTestWirePayload):
    type: Literal["short_answer"]
    expected_answer: str


class _McqSingleWirePayload(_BaseGeneratedTestWirePayload):
    type: Literal["mcq_single"]
    options: list[_WireMcqOption]
    correct_option_ids: list[str]


class _McqMultiWirePayload(_BaseGeneratedTestWirePayload):
    type: Literal["mcq_multi"]
    options: list[_WireMcqOption]
    correct_option_ids: list[str]

_generated_test_payload_adapter = TypeAdapter(GeneratedTestPayload)
_generated_test_public_payload_adapter = TypeAdapter(GeneratedTestPublicPayload)
_generated_test_grading_payload_adapter = TypeAdapter(GeneratedTestGradingPayload)
_submitted_answer_payload_adapter = TypeAdapter(SubmittedAnswerPayload)
_generated_test_payload_adapters: dict[GeneratedTestType, TypeAdapter[Any]] = {
    "short_answer": TypeAdapter(ShortAnswerTestPayload),
    "mcq_single": TypeAdapter(McqSingleTestPayload),
    "mcq_multi": TypeAdapter(McqMultiTestPayload),
}
_generated_test_wire_adapters: dict[GeneratedTestType, TypeAdapter[Any]] = {
    "short_answer": TypeAdapter(_ShortAnswerWirePayload),
    "mcq_single": TypeAdapter(_McqSingleWirePayload),
    "mcq_multi": TypeAdapter(_McqMultiWirePayload),
}


def validate_generated_test_payload(
    payload: dict[str, Any],
    *,
    expected_type: GeneratedTestType | None = None,
) -> GeneratedTestPayload:
    adapter = (
        _generated_test_payload_adapter
        if expected_type is None
        else _generated_test_payload_adapters[expected_type]
    )
    return adapter.validate_python(payload)


def generated_test_payload_json_schema(
    test_type: GeneratedTestType | None = None,
) -> dict[str, Any]:
    adapter = (
        _generated_test_payload_adapter
        if test_type is None
        else _generated_test_payload_adapters[test_type]
    )
    return adapter.json_schema()


def generated_test_wire_json_schema(
    test_type: GeneratedTestType,
    *,
    evidence_source_uris: list[str] | None = None,
) -> dict[str, Any]:
    """Return the small generation contract sent to a model provider.

    Canonical length and collection bounds remain on the persisted payload models and
    are enforced after generation. They are intentionally absent here because local
    grammar compilers should only receive the shape the model must author.
    """

    schema = _generated_test_wire_adapters[test_type].json_schema()
    source_uris = list(dict.fromkeys(evidence_source_uris or []))
    if source_uris:
        schema["properties"]["evidence_refs"]["items"]["enum"] = source_uris
    return schema


def normalize_generated_test_payload(
    payload: dict[str, Any],
    *,
    expected_type: GeneratedTestType | None = None,
) -> dict[str, Any]:
    validated_payload = validate_generated_test_payload(
        coerce_generated_test_payload(payload),
        expected_type=expected_type,
    )
    return validated_payload.model_dump(mode="json", exclude_none=True)


def generated_test_public_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the browser-safe presentation view of a canonical generated test."""

    generated_test = validate_generated_test_payload(payload)
    public_payload: dict[str, Any] = {
        "schema_version": generated_test.schema_version,
        "type": generated_test.type,
        "question": generated_test.question,
        "evidence_refs": [
            evidence_ref.model_dump(mode="json", exclude={"metadata"}, exclude_none=True)
            for evidence_ref in generated_test.evidence_refs
        ],
    }
    if isinstance(generated_test, McqSingleTestPayload | McqMultiTestPayload):
        public_payload["options"] = [
            option.model_dump(mode="json") for option in generated_test.options
        ]
    validated_payload = _generated_test_public_payload_adapter.validate_python(public_payload)
    return validated_payload.model_dump(mode="json", exclude_none=True)


def generated_test_grading_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return grading-only fields for judging and post-submission disclosure."""

    generated_test = validate_generated_test_payload(payload)
    grading_payload: dict[str, Any] = {
        "type": generated_test.type,
        "rubric": [
            criterion.model_dump(mode="json") for criterion in generated_test.rubric
        ],
        "explanation": generated_test.explanation,
    }
    if isinstance(generated_test, ShortAnswerTestPayload):
        grading_payload["expected_answer"] = generated_test.expected_answer
    else:
        grading_payload["correct_option_ids"] = generated_test.correct_option_ids
    validated_payload = _generated_test_grading_payload_adapter.validate_python(grading_payload)
    return validated_payload.model_dump(mode="json", exclude_none=True)


def normalize_submitted_answer_payload(
    payload: dict[str, Any],
    *,
    generated_test_payload: dict[str, Any],
) -> dict[str, Any]:
    """Validate an answer against the generated test's type and visible options."""

    generated_test = validate_generated_test_payload(generated_test_payload)
    submitted_answer = _submitted_answer_payload_adapter.validate_python(payload)
    if submitted_answer.type != generated_test.type:
        raise ValueError(
            f"answer type {submitted_answer.type!r} does not match "
            f"generated test type {generated_test.type!r}"
        )

    if isinstance(
        submitted_answer,
        McqSingleSubmissionPayload | McqMultiSubmissionPayload,
    ):
        valid_option_ids = {option.id for option in generated_test.options}
        selected_option_ids = (
            [submitted_answer.selected_option_id]
            if isinstance(submitted_answer, McqSingleSubmissionPayload)
            else submitted_answer.selected_option_ids
        )
        invalid_option_ids = sorted(set(selected_option_ids) - valid_option_ids)
        if invalid_option_ids:
            raise ValueError(
                "selected option ids are not present in options: "
                + ", ".join(invalid_option_ids)
            )

    return submitted_answer.model_dump(mode="json")


def coerce_generated_test_payload(payload: dict[str, Any]) -> dict[str, Any]:
    coerced_payload = dict(payload)
    coerced_payload["evidence_refs"] = coerce_evidence_refs(coerced_payload.get("evidence_refs", []))

    test_type = coerced_payload.get("type")
    if test_type in {"mcq_single", "mcq_multi"}:
        coerced_payload = coerce_mcq_payload(coerced_payload)

    return coerced_payload


def coerce_evidence_refs(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return value

    evidence_refs: list[Any] = []
    for item in value:
        if isinstance(item, str):
            evidence_refs.append(
                {
                    "source_type": source_type_from_uri(item),
                    "source_uri": item,
                }
            )
            continue
        if isinstance(item, dict):
            evidence_ref = dict(item)
            source_uri = evidence_ref.get("source_uri")
            if isinstance(source_uri, str) and not evidence_ref.get("source_type"):
                evidence_ref["source_type"] = source_type_from_uri(source_uri)
            evidence_refs.append(evidence_ref)
            continue
        evidence_refs.append(item)
    return evidence_refs


def coerce_mcq_payload(payload: dict[str, Any]) -> dict[str, Any]:
    coerced_payload = dict(payload)
    coerced_options = coerce_mcq_options(coerced_payload.get("options", []))
    coerced_payload["options"] = coerced_options

    if "correct_option_ids" not in coerced_payload:
        answer = coerced_payload.get("answer", coerced_payload.get("correct_answer"))
        correct_option_ids = correct_option_ids_from_answer(answer, coerced_options)
        if correct_option_ids:
            coerced_payload["correct_option_ids"] = correct_option_ids

    coerced_payload.pop("answer", None)
    coerced_payload.pop("correct_answer", None)
    return coerced_payload


def coerce_mcq_options(value: Any) -> Any:
    if not isinstance(value, list):
        return value

    option_ids = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    options: list[Any] = []
    for index, item in enumerate(value):
        if isinstance(item, str):
            option_id = option_ids[index] if index < len(option_ids) else str(index + 1)
            options.append({"id": option_id, "text": item})
            continue
        options.append(item)
    return options


def correct_option_ids_from_answer(answer: Any, options: list[Any]) -> list[str]:
    if answer is None:
        return []

    answers = answer if isinstance(answer, list) else [answer]
    option_ids: list[str] = []
    for raw_answer in answers:
        if not isinstance(raw_answer, str):
            continue
        normalized_answer = raw_answer.strip().lower()
        for option in options:
            if not isinstance(option, dict):
                continue
            option_id = option.get("id")
            option_text = option.get("text")
            if not isinstance(option_id, str) or not isinstance(option_text, str):
                continue
            if normalized_answer in {option_id.strip().lower(), option_text.strip().lower()}:
                option_ids.append(option_id)
                break
    return option_ids


def source_type_from_uri(source_uri: str) -> str:
    prefix, separator, _rest = source_uri.partition(":")
    if separator and prefix:
        return prefix
    return "code"


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
