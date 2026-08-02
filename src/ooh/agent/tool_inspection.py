from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ooh.agent.prompt_budget import (
    DEFAULT_TOOL_OBSERVATION_MAX_BYTES,
    compact_tool_observation,
)
from ooh.agent.tools import ToolExecution

@dataclass(frozen=True)
class FailedToolCall:
    call_id: str
    tool_name: str
    arguments: dict[str, Any]
    error_type: str
    error: str


@dataclass(frozen=True)
class DuplicateToolCall:
    call_id: str
    tool_name: str
    arguments: dict[str, Any]
    signature: str


def inspection_prompt_payload(
    *,
    model_turn_count: int,
    executions: list[ToolExecution],
    failed_calls: list[FailedToolCall],
    duplicate_calls: list[DuplicateToolCall],
    max_bytes: int = DEFAULT_TOOL_OBSERVATION_MAX_BYTES,
) -> dict[str, Any]:
    payload = {
        "schema_version": 2,
        "model_turn_count": model_turn_count,
        "completed_call_count": len(executions),
        "failed_call_count": len(failed_calls),
        "duplicate_call_count": len(duplicate_calls),
        "tool_calls": [
            {
                "call_id": execution.invocation.call_id,
                "tool_name": execution.invocation.tool_name,
                "arguments": execution.invocation.arguments,
                "duration_ms": execution.duration_ms,
                "payload": execution.result.payload,
                "evidence_refs": execution.result.evidence_refs,
            }
            for execution in executions
        ],
        "failed_tool_calls": [
            {
                "call_id": failed_call.call_id,
                "tool_name": failed_call.tool_name,
                "arguments": failed_call.arguments,
                "error_type": failed_call.error_type,
                "error": failed_call.error,
            }
            for failed_call in failed_calls
        ],
        "duplicate_tool_calls": [
            {
                "call_id": duplicate_call.call_id,
                "tool_name": duplicate_call.tool_name,
                "arguments": duplicate_call.arguments,
                "error": "identical tool call already executed",
            }
            for duplicate_call in duplicate_calls
        ],
    }
    return compact_tool_observation(payload, max_bytes=max_bytes)


def context_pack_with_tool_inspection(
    context_pack: dict[str, Any],
    tool_inspection: dict[str, Any],
) -> dict[str, Any]:
    if not any(
        tool_inspection.get(key)
        for key in ("tool_calls", "failed_tool_calls", "duplicate_tool_calls")
    ):
        return context_pack
    return {**context_pack, "tool_inspection": tool_inspection}


def snapshot_index_uri(context_pack: dict[str, Any]) -> str | None:
    snapshot = context_pack.get("snapshot")
    if not isinstance(snapshot, dict):
        return None
    index_uri = snapshot.get("index_uri")
    if not isinstance(index_uri, str) or not index_uri:
        return None
    return index_uri


def snapshot_id(context_pack: dict[str, Any]) -> str | None:
    snapshot = context_pack.get("snapshot")
    if not isinstance(snapshot, dict):
        return None
    value = snapshot.get("id")
    if not isinstance(value, str) or not value:
        return None
    return value
