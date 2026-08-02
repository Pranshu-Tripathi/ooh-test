from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EvidenceIssue:
    code: str
    message: str
    source_uri: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "code": self.code,
            "message": self.message,
            "source_uri": self.source_uri,
        }


@dataclass(frozen=True)
class EvidenceVerificationResult:
    payload: dict[str, Any]
    accepted_refs: list[dict[str, Any]]
    rejected_refs: list[EvidenceIssue]
    available_refs: list[dict[str, Any]]

    @property
    def is_valid(self) -> bool:
        return not self.rejected_refs

    def error_message(self) -> str:
        if self.is_valid:
            return ""

        issue_messages = "; ".join(issue.message for issue in self.rejected_refs[:5])
        if len(self.rejected_refs) > 5:
            issue_messages = f"{issue_messages}; {len(self.rejected_refs) - 5} more issue(s)"
        return f"evidence verification failed: {issue_messages}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "accepted_refs": self.accepted_refs,
            "rejected_refs": [issue.to_dict() for issue in self.rejected_refs],
            "available_refs": self.available_refs,
        }


def verify_generated_test_evidence(
    payload: dict[str, Any],
    context_pack: dict[str, Any],
) -> EvidenceVerificationResult:
    available_refs_by_uri = collect_available_evidence_refs(context_pack)
    evidence_refs = payload.get("evidence_refs", [])
    accepted_refs: list[dict[str, Any]] = []
    rejected_refs: list[EvidenceIssue] = []

    if available_refs_by_uri and not evidence_refs:
        rejected_refs.append(
            EvidenceIssue(
                code="missing_evidence_refs",
                message="generated test must cite at least one context-pack source ref",
            )
        )

    for evidence_ref in evidence_refs:
        if not isinstance(evidence_ref, dict):
            rejected_refs.append(
                EvidenceIssue(
                    code="invalid_evidence_ref",
                    message="evidence ref must be an object",
                )
            )
            continue

        source_uri = _string(evidence_ref.get("source_uri"))
        if source_uri is None:
            rejected_refs.append(
                EvidenceIssue(
                    code="missing_source_uri",
                    message="evidence ref is missing source_uri",
                )
            )
            continue

        available_ref = available_refs_by_uri.get(source_uri)
        if available_ref is None:
            rejected_refs.append(
                EvidenceIssue(
                    code="unknown_source_uri",
                    source_uri=source_uri,
                    message=f"evidence source_uri is not present in the context pack: {source_uri}",
                )
            )
            continue

        source_type = _string(evidence_ref.get("source_type"))
        available_source_type = _string(available_ref.get("source_type"))
        if source_type != available_source_type:
            rejected_refs.append(
                EvidenceIssue(
                    code="source_type_mismatch",
                    source_uri=source_uri,
                    message=(
                        "evidence source_type does not match context pack "
                        f"for {source_uri}: expected {available_source_type}, got {source_type}"
                    ),
                )
            )
            continue

        content_hash = _string(evidence_ref.get("content_hash"))
        available_content_hash = _string(available_ref.get("content_hash"))
        if content_hash is not None and available_content_hash is None:
            rejected_refs.append(
                EvidenceIssue(
                    code="content_hash_unavailable",
                    source_uri=source_uri,
                    message=f"evidence content_hash is not available in the context pack: {source_uri}",
                )
            )
            continue
        if content_hash is not None and content_hash != available_content_hash:
            rejected_refs.append(
                EvidenceIssue(
                    code="content_hash_mismatch",
                    source_uri=source_uri,
                    message=f"evidence content_hash does not match context pack source: {source_uri}",
                )
            )
            continue

        accepted_refs.append(_enrich_evidence_ref(evidence_ref, available_ref))

    verified_payload = {**payload, "evidence_refs": accepted_refs}
    return EvidenceVerificationResult(
        payload=verified_payload,
        accepted_refs=accepted_refs,
        rejected_refs=rejected_refs,
        available_refs=[
            available_refs_by_uri[source_uri]
            for source_uri in sorted(available_refs_by_uri)
        ],
    )


def collect_available_evidence_refs(context_pack: dict[str, Any]) -> dict[str, dict[str, Any]]:
    available_refs: dict[str, dict[str, Any]] = {}

    for source_ref in _dicts(context_pack.get("source_refs")):
        _add_available_ref(
            available_refs,
            source_type=_string(source_ref.get("source_type")),
            source_uri=_string(source_ref.get("source_uri")),
            content_hash=_string(source_ref.get("content_hash")),
        )

    for file in _dicts(context_pack.get("included_files")):
        path = _string(file.get("path"))
        _add_available_ref(
            available_refs,
            source_type="code",
            source_uri=f"code:{path}" if path is not None else None,
            content_hash=_string(file.get("sha256")),
        )

    for guidance in _dicts(context_pack.get("included_guidance")):
        path = _string(guidance.get("path"))
        _add_available_ref(
            available_refs,
            source_type="guidance",
            source_uri=f"guidance:{path}" if path is not None else None,
            content_hash=_string(guidance.get("content_hash")),
        )

    tool_inspection = context_pack.get("tool_inspection")
    if isinstance(tool_inspection, dict):
        for tool_call in _dicts(tool_inspection.get("tool_calls")):
            for evidence_ref in _dicts(tool_call.get("evidence_refs")):
                _add_available_ref(
                    available_refs,
                    source_type=_string(evidence_ref.get("source_type")),
                    source_uri=_string(evidence_ref.get("source_uri")),
                    content_hash=_string(evidence_ref.get("content_hash")),
                )

    drift = context_pack.get("drift")
    if isinstance(drift, dict):
        drift_id = _string(drift.get("id"))
        _add_available_ref(
            available_refs,
            source_type="drift",
            source_uri=f"drift_event:{drift_id}" if drift_id is not None else None,
            content_hash=None,
        )

    attention_profile = context_pack.get("attention_profile")
    if isinstance(attention_profile, dict):
        attention_profile_id = _string(attention_profile.get("id"))
        _add_available_ref(
            available_refs,
            source_type="attention_profile",
            source_uri=(
                f"attention_profile:{attention_profile_id}"
                if attention_profile_id is not None
                else None
            ),
            content_hash=None,
        )

    return available_refs


def _add_available_ref(
    available_refs: dict[str, dict[str, Any]],
    *,
    source_type: str | None,
    source_uri: str | None,
    content_hash: str | None,
) -> None:
    if source_type is None or source_uri is None:
        return

    existing_ref = available_refs.get(source_uri)
    if existing_ref is None:
        available_refs[source_uri] = {
            "source_type": source_type,
            "source_uri": source_uri,
            "content_hash": content_hash,
        }
        return

    if existing_ref.get("content_hash") is None and content_hash is not None:
        existing_ref["content_hash"] = content_hash


def _enrich_evidence_ref(
    evidence_ref: dict[str, Any],
    available_ref: dict[str, Any],
) -> dict[str, Any]:
    enriched = dict(evidence_ref)
    enriched["source_type"] = available_ref["source_type"]
    enriched["source_uri"] = available_ref["source_uri"]
    if available_ref.get("content_hash") is not None:
        enriched["content_hash"] = available_ref["content_hash"]
    return enriched


def _dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
