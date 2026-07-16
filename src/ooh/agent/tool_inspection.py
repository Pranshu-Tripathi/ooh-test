from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ooh.agent.tools import ToolExecution

MAX_INSPECT_CODE_PATHS = 3
READ_FILE_RANGE_MAX_LINES = 80
READ_FILE_RANGE_MAX_BYTES = 8_000


@dataclass(frozen=True)
class PlannedToolCall:
    call_id: str
    tool_name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class FailedToolCall:
    call_id: str
    tool_name: str
    arguments: dict[str, Any]
    error_type: str
    error: str


def build_inspection_plan(context_pack: dict[str, Any]) -> list[PlannedToolCall]:
    if not snapshot_index_uri(context_pack):
        return []

    target_paths = code_paths_from_context_pack(context_pack)
    planned_calls = [
        PlannedToolCall(
            call_id="inspect-list-python-files",
            tool_name="repo.list_files",
            arguments={
                "language": "python",
                "parse_status": "parsed",
                "limit": 25,
            },
        )
    ]
    for index, path in enumerate(target_paths[:MAX_INSPECT_CODE_PATHS], start=1):
        planned_calls.append(
            PlannedToolCall(
                call_id=f"inspect-symbols-{index}",
                tool_name="repo.list_symbols",
                arguments={"path": path, "limit": 50},
            )
        )
        planned_calls.append(
            PlannedToolCall(
                call_id=f"inspect-source-{index}",
                tool_name="repo.read_file_range",
                arguments={
                    "path": path,
                    "start_line": 1,
                    "max_lines": READ_FILE_RANGE_MAX_LINES,
                    "max_bytes": READ_FILE_RANGE_MAX_BYTES,
                },
            )
        )
    return planned_calls


def inspection_prompt_payload(
    *,
    planned_calls: list[PlannedToolCall],
    executions: list[ToolExecution],
    failed_calls: list[FailedToolCall],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "planned_call_count": len(planned_calls),
        "completed_call_count": len(executions),
        "failed_call_count": len(failed_calls),
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
    }


def context_pack_with_tool_inspection(
    context_pack: dict[str, Any],
    tool_inspection: dict[str, Any],
) -> dict[str, Any]:
    if not tool_inspection.get("tool_calls") and not tool_inspection.get("failed_tool_calls"):
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


def code_paths_from_context_pack(context_pack: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()

    for file in dict_items(context_pack.get("included_files")):
        add_path(paths, seen, file.get("path"))

    for source_ref in dict_items(context_pack.get("source_refs")):
        source_uri = source_ref.get("source_uri")
        if isinstance(source_uri, str) and source_uri.startswith("code:"):
            add_path(paths, seen, source_uri.removeprefix("code:"))

    return paths


def add_path(paths: list[str], seen: set[str], value: object) -> None:
    if not isinstance(value, str):
        return
    path = value.strip()
    if not path or path in seen:
        return
    seen.add(path)
    paths.append(path)


def dict_items(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]
