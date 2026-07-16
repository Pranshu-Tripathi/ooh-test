from __future__ import annotations

from typing import Any

from ooh.agent.tools.base import RepoTool, RepoToolArgs
from ooh.agent.tools.common import (
    DEFAULT_LIST_FILES_LIMIT,
    RepoToolResult,
    RepositoryToolContext,
    bounded_limit,
    file_summary,
    normalize_relative_prefix,
)

TOOL_NAME = "repo.list_files"


class ListFilesArgs(RepoToolArgs):
    path_prefix: str | None = None,
    language: str | None = None,
    parse_status: str | None = None,
    limit: int | None = None,


class ListFilesTool(RepoTool[ListFilesArgs]):
    name = TOOL_NAME
    description = "List indexed repository files with optional path, language, and parse-status filters."
    args_model = ListFilesArgs

    def run(self, context: RepositoryToolContext, args: ListFilesArgs) -> RepoToolResult:
        max_results = bounded_limit(
            args.limit,
            default=DEFAULT_LIST_FILES_LIMIT,
            maximum=DEFAULT_LIST_FILES_LIMIT,
        )
        normalized_prefix = normalize_relative_prefix(args.path_prefix)
        files = sorted(context.files, key=lambda file: str(file.get("path", "")))

        filtered_files = [
            file
            for file in files
            if matches_path_prefix(file, normalized_prefix)
            and matches_optional_string(file.get("language"), args.language)
            and matches_optional_string(file.get("parse_status"), args.parse_status)
        ]
        selected_files = filtered_files[:max_results]
        payload: dict[str, Any] = {
            "files": [file_summary(file) for file in selected_files],
            "total_matches": len(filtered_files),
            "truncated": len(filtered_files) > len(selected_files),
            "limit": max_results,
            "filters": {
                "path_prefix": normalized_prefix,
                "language": args.language,
                "parse_status": args.parse_status,
            },
        }
        return RepoToolResult(tool_name=self.name, payload=payload, evidence_refs=[])


TOOL = ListFilesTool()


def run(
    context: RepositoryToolContext,
    *,
    path_prefix: str | None = None,
    language: str | None = None,
    parse_status: str | None = None,
    limit: int | None = None,
) -> RepoToolResult:
    return TOOL.run(
        context,
        ListFilesArgs(
            path_prefix=path_prefix,
            language=language,
            parse_status=parse_status,
            limit=limit,
        ),
    )


def matches_path_prefix(file: dict[str, Any], path_prefix: str | None) -> bool:
    if path_prefix is None:
        return True

    path = file.get("path")
    if not isinstance(path, str):
        return False
    return path == path_prefix or path.startswith(f"{path_prefix}/")


def matches_optional_string(value: object, expected: str | None) -> bool:
    if expected is None:
        return True
    return value == expected
