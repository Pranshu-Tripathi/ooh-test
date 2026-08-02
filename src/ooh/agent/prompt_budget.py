from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

DEFAULT_GENERATION_PROMPT_MAX_BYTES = 8_000
DEFAULT_TOOL_OBSERVATION_MAX_BYTES = 2_500
MAX_PROMPT_SOURCE_REFS = 24
MAX_PROMPT_FILES = 10
MAX_PROMPT_GUIDANCE = 6
MAX_PROMPT_FOCUS_AREAS = 8
MAX_PROMPT_FILE_EXCERPT_BYTES = 1_400
MAX_PROMPT_GUIDANCE_EXCERPT_BYTES = 1_000
TRUNCATION_MARKER = "\n...[truncated]"


@dataclass(frozen=True)
class PromptContextView:
    payload: dict[str, Any]
    original_bytes: int
    used_bytes: int
    max_bytes: int
    truncated: bool


def build_prompt_context_view(
    context_pack: dict[str, Any],
    *,
    max_bytes: int,
    tool_observation_max_bytes: int = DEFAULT_TOOL_OBSERVATION_MAX_BYTES,
) -> PromptContextView:
    if max_bytes < 1_000:
        raise ValueError("prompt context max_bytes must be at least 1000")
    if tool_observation_max_bytes < 250:
        raise ValueError("tool observation max_bytes must be at least 250")

    original_bytes = json_size_bytes(context_pack)
    payload: dict[str, Any] = {
        "schema_version": context_pack.get("schema_version", 1),
        "pack_type": context_pack.get("pack_type"),
        "purpose": truncate_text_bytes(_string(context_pack.get("purpose")), 500),
        "repository": _selected_mapping(
            context_pack.get("repository"),
            ("name", "source_type"),
        ),
        "snapshot": _selected_mapping(
            context_pack.get("snapshot"),
            ("commit_sha",),
        ),
        "summary": _scalar_mapping(context_pack.get("summary"), max_items=12),
        "source_refs": [],
        "included_files": [],
        "included_guidance": [],
    }
    truncated = set(context_pack) - {
        "schema_version",
        "pack_type",
        "purpose",
        "repository",
        "snapshot",
        "summary",
        "source_refs",
        "included_files",
        "included_guidance",
        "drift",
        "attention_profile",
        "tool_inspection",
        "prompt_content",
    } != set()

    drift = _compact_drift(context_pack.get("drift"))
    if drift is not None:
        if not _try_set(payload, "drift", drift, max_bytes=max_bytes):
            truncated = True

    attention_profile = _compact_attention_profile(context_pack.get("attention_profile"))
    if attention_profile is not None:
        if not _try_set(
            payload,
            "attention_profile",
            attention_profile,
            max_bytes=max_bytes,
        ):
            truncated = True

    source_refs = _dict_items(context_pack.get("source_refs"))
    for source_ref in source_refs[:MAX_PROMPT_SOURCE_REFS]:
        compact_ref = _selected_mapping(source_ref, ("source_type", "source_uri"))
        if not _try_append(payload, "source_refs", compact_ref, max_bytes=max_bytes):
            truncated = True
            break
    if len(source_refs) > len(payload["source_refs"]):
        truncated = True

    tool_inspection = context_pack.get("tool_inspection")
    if isinstance(tool_inspection, dict):
        compact_tool_inspection = compact_tool_observation(
            tool_inspection,
            max_bytes=tool_observation_max_bytes,
        )
        if not _try_set(
            payload,
            "tool_inspection",
            compact_tool_inspection,
            max_bytes=max_bytes,
        ):
            truncated = True
        if compact_tool_inspection != tool_inspection:
            truncated = True

    files = _dict_items(context_pack.get("included_files"))
    for file in files[:MAX_PROMPT_FILES]:
        compact_file, file_truncated = _compact_file(file)
        if _try_append(payload, "included_files", compact_file, max_bytes=max_bytes):
            truncated = truncated or file_truncated
            continue

        metadata_only = {key: value for key, value in compact_file.items() if key != "content_excerpt"}
        if _try_append(payload, "included_files", metadata_only, max_bytes=max_bytes):
            truncated = True
            continue

        truncated = True
        break
    if len(files) > len(payload["included_files"]):
        truncated = True

    guidance_items = _dict_items(context_pack.get("included_guidance"))
    for guidance in guidance_items[:MAX_PROMPT_GUIDANCE]:
        compact_guidance, guidance_truncated = _compact_guidance(guidance)
        if _try_append(
            payload,
            "included_guidance",
            compact_guidance,
            max_bytes=max_bytes,
        ):
            truncated = truncated or guidance_truncated
            continue

        metadata_only = {
            key: value for key, value in compact_guidance.items() if key != "content_excerpt"
        }
        if _try_append(
            payload,
            "included_guidance",
            metadata_only,
            max_bytes=max_bytes,
        ):
            truncated = True
            continue

        truncated = True
        break
    if len(guidance_items) > len(payload["included_guidance"]):
        truncated = True

    used_bytes = json_size_bytes(payload)
    if used_bytes > max_bytes:
        raise ValueError("prompt context projection exceeded its byte budget")
    return PromptContextView(
        payload=payload,
        original_bytes=original_bytes,
        used_bytes=used_bytes,
        max_bytes=max_bytes,
        truncated=truncated or used_bytes < original_bytes,
    )


def serialize_prompt_context(view: PromptContextView) -> str:
    return json.dumps(
        view.payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def json_size_bytes(value: Any) -> int:
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )


def truncate_text_bytes(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    marker = TRUNCATION_MARKER.encode("utf-8")
    if max_bytes <= len(marker):
        return marker[:max_bytes].decode("utf-8", errors="ignore")
    prefix = encoded[: max_bytes - len(marker)].decode("utf-8", errors="ignore")
    return f"{prefix}{TRUNCATION_MARKER}"


def _try_set(payload: dict[str, Any], key: str, value: Any, *, max_bytes: int) -> bool:
    had_key = key in payload
    previous = payload.get(key)
    payload[key] = value
    if json_size_bytes(payload) <= max_bytes:
        return True
    if had_key:
        payload[key] = previous
    else:
        payload.pop(key, None)
    return False


def _try_append(
    payload: dict[str, Any],
    key: str,
    value: Any,
    *,
    max_bytes: int,
) -> bool:
    values = payload[key]
    if not isinstance(values, list):
        raise TypeError(f"prompt payload field is not a list: {key}")
    values.append(value)
    if json_size_bytes(payload) <= max_bytes:
        return True
    values.pop()
    return False


def _compact_file(file: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    compact = _selected_mapping(
        file,
        ("path", "status", "language", "parse_status", "size_bytes", "insertions", "deletions"),
    )
    excerpt = file.get("content_excerpt")
    if not isinstance(excerpt, dict):
        return compact, False

    text = excerpt.get("text")
    if not isinstance(text, str):
        compact["content_excerpt"] = _selected_mapping(
            excerpt,
            ("omitted", "omitted_reason"),
        )
        return compact, False

    bounded_text = truncate_text_bytes(text, MAX_PROMPT_FILE_EXCERPT_BYTES)
    compact["content_excerpt"] = {
        "text": bounded_text,
        "start_line": excerpt.get("start_line"),
        "end_line": excerpt.get("end_line"),
        "truncated": excerpt.get("truncated") is True or bounded_text != text,
    }
    return compact, bounded_text != text


def _compact_guidance(guidance: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    compact = _selected_mapping(guidance, ("path", "source_type"))
    excerpt = guidance.get("content_excerpt")
    if not isinstance(excerpt, dict):
        return compact, False

    text = excerpt.get("text")
    if not isinstance(text, str):
        compact["content_excerpt"] = _selected_mapping(
            excerpt,
            ("omitted", "omitted_reason"),
        )
        return compact, False

    bounded_text = truncate_text_bytes(text, MAX_PROMPT_GUIDANCE_EXCERPT_BYTES)
    compact["content_excerpt"] = {
        "text": bounded_text,
        "start_line": excerpt.get("start_line"),
        "end_line": excerpt.get("end_line"),
        "truncated": excerpt.get("truncated") is True or bounded_text != text,
    }
    return compact, bounded_text != text


def _compact_drift(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    compact = _selected_mapping(
        value,
        ("id", "from_commit_sha", "to_commit_sha", "drift_score", "severity"),
    )
    breakdown = value.get("breakdown")
    if isinstance(breakdown, dict):
        compact_breakdown = _scalar_mapping(breakdown, max_items=12)
        changed_files = _dict_items(breakdown.get("changed_files"))
        if changed_files:
            compact_breakdown["changed_files"] = [
                _selected_mapping(
                    file,
                    ("path", "status", "insertions", "deletions", "language"),
                )
                for file in changed_files[:12]
            ]
        compact["breakdown"] = compact_breakdown
    return compact


def _compact_attention_profile(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    compact = _selected_mapping(value, ("id", "name", "default_weight"))
    focus_areas = _dict_items(value.get("focus_areas"))
    if focus_areas:
        compact["focus_areas"] = [
            {
                **_selected_mapping(area, ("name", "weight")),
                "path_globs": _string_items(area.get("path_globs"))[:12],
            }
            for area in focus_areas[:MAX_PROMPT_FOCUS_AREAS]
        ]
    return compact


def compact_tool_observation(value: dict[str, Any], *, max_bytes: int) -> dict[str, Any]:
    if json_size_bytes(value) <= max_bytes:
        return value
    compact = _selected_mapping(
        value,
        (
            "schema_version",
            "model_turn_count",
            "completed_call_count",
            "failed_call_count",
            "duplicate_call_count",
        ),
    )
    compact["truncated"] = True
    compact["failed_tool_calls"] = []
    for failed_call in _dict_items(value.get("failed_tool_calls")):
        compact_failure = _selected_mapping(
            failed_call,
            ("call_id", "tool_name", "arguments", "error_type", "error"),
        )
        if not _try_append(
            compact,
            "failed_tool_calls",
            compact_failure,
            max_bytes=max_bytes,
        ):
            break

    compact["duplicate_tool_calls"] = []
    for duplicate_call in _dict_items(value.get("duplicate_tool_calls")):
        compact_duplicate = _selected_mapping(
            duplicate_call,
            ("call_id", "tool_name", "arguments", "error"),
        )
        if not _try_append(
            compact,
            "duplicate_tool_calls",
            compact_duplicate,
            max_bytes=max_bytes,
        ):
            break

    tool_calls = _dict_items(value.get("tool_calls"))
    compact["tool_calls"] = []
    per_call_bytes = max(200, max_bytes // max(len(tool_calls), 1) // 2)
    for tool_call in tool_calls:
        payload_value = tool_call.get("payload", tool_call.get("payload_json", {}))
        payload_json = (
            payload_value
            if isinstance(payload_value, str)
            else json.dumps(payload_value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        )
        compact_call = {
            **_selected_mapping(tool_call, ("call_id", "tool_name", "arguments")),
            "payload_json": truncate_text_bytes(payload_json, per_call_bytes),
            "evidence_refs": [
                _selected_mapping(evidence_ref, ("source_type", "source_uri"))
                for evidence_ref in _dict_items(tool_call.get("evidence_refs"))[:8]
            ],
        }
        if not _try_append(compact, "tool_calls", compact_call, max_bytes=max_bytes):
            break
    return compact


def _selected_mapping(value: object, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {key: value[key] for key in keys if value.get(key) is not None}


def _scalar_mapping(value: object, *, max_items: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    compact: dict[str, Any] = {}
    for key in sorted(value):
        item = value[key]
        if item is None or isinstance(item, bool | int | float):
            compact[key] = item
        elif isinstance(item, str):
            compact[key] = truncate_text_bytes(item, 300)
        elif isinstance(item, list) and all(isinstance(entry, str) for entry in item):
            compact[key] = [truncate_text_bytes(entry, 200) for entry in item[:12]]
        if len(compact) >= max_items:
            break
    return compact


def _dict_items(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _string_items(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""
