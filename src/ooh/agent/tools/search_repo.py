from __future__ import annotations

from typing import Any

from ooh.agent.tools.base import RepoTool, RepoToolArgs
from ooh.agent.tools.common import (
    DEFAULT_SEARCH_FILE_BYTES,
    DEFAULT_SEARCH_LINE_BYTES,
    DEFAULT_SEARCH_RESULT_LIMIT,
    RepoToolError,
    RepoToolResult,
    RepositoryToolContext,
    bounded_limit,
    code_evidence_ref,
    is_unsafe_path,
    normalize_relative_path,
    normalize_relative_prefix,
    redact_secret_line,
    truncate_to_bytes,
)

TOOL_NAME = "repo.search_repo"


class SearchRepoArgs(RepoToolArgs):
    query: str
    path_prefix: str | None = None,
    language: str | None = None,
    case_sensitive: bool = False,
    limit: int | None = None,


class SearchRepoTool(RepoTool[SearchRepoArgs]):
    name = TOOL_NAME
    description = "Search indexed repository text with bounded literal line matches."
    args_model = SearchRepoArgs

    def run(self, context: RepositoryToolContext, args: SearchRepoArgs) -> RepoToolResult:
        stripped_query = args.query.strip()
        if not stripped_query:
            raise RepoToolError("query must not be blank")

        max_results = bounded_limit(
            args.limit,
            default=DEFAULT_SEARCH_RESULT_LIMIT,
            maximum=DEFAULT_SEARCH_RESULT_LIMIT,
        )
        normalized_prefix = normalize_relative_prefix(args.path_prefix)
        results: list[dict[str, Any]] = []
        evidence_refs: list[dict[str, Any]] = []
        skipped_files: list[dict[str, str]] = []
        truncated = False

        for file in sorted(context.files, key=lambda item: str(item.get("path", ""))):
            path = file.get("path")
            if not isinstance(path, str):
                continue
            if not matches_filters(file, path_prefix=normalized_prefix, language=args.language):
                continue

            skip_reason = file_skip_reason(file)
            if skip_reason is not None:
                skipped_files.append({"path": path, "reason": skip_reason})
                continue

            source_path = context.source_path_for_file(normalize_relative_path(path))
            if not source_path.is_file():
                skipped_files.append({"path": path, "reason": "file_unavailable"})
                continue

            with source_path.open("r", encoding="utf-8", errors="replace") as source_file:
                for line_number, raw_line in enumerate(source_file, start=1):
                    text = raw_line.rstrip("\n\r")
                    if "\0" in text:
                        skipped_files.append({"path": path, "reason": "binary_file"})
                        break
                    if not line_matches(text, stripped_query, case_sensitive=args.case_sensitive):
                        continue

                    redacted_text = redact_secret_line(text)
                    result = {
                        "path": path,
                        "line_number": line_number,
                        "line": truncate_to_bytes(redacted_text, DEFAULT_SEARCH_LINE_BYTES),
                        "line_truncated": (
                            len(redacted_text.encode("utf-8")) > DEFAULT_SEARCH_LINE_BYTES
                        ),
                        "sha256": file.get("sha256"),
                    }
                    results.append(result)
                    evidence_refs.append(
                        code_evidence_ref(
                            context,
                            file=file,
                            start_line=line_number,
                            end_line=line_number,
                            tool_name=self.name,
                        )
                    )

                    if len(results) >= max_results:
                        truncated = True
                        break
            if truncated:
                break

        payload: dict[str, Any] = {
            "query": stripped_query,
            "case_sensitive": args.case_sensitive,
            "results": results,
            "returned_matches": len(results),
            "truncated": truncated,
            "limit": max_results,
            "filters": {
                "path_prefix": normalized_prefix,
                "language": args.language,
            },
            "skipped_files": skipped_files[:25],
        }
        return RepoToolResult(tool_name=self.name, payload=payload, evidence_refs=evidence_refs)


TOOL = SearchRepoTool()


def run(
    context: RepositoryToolContext,
    *,
    query: str,
    path_prefix: str | None = None,
    language: str | None = None,
    case_sensitive: bool = False,
    limit: int | None = None,
) -> RepoToolResult:
    return TOOL.run(
        context,
        SearchRepoArgs(
            query=query,
            path_prefix=path_prefix,
            language=language,
            case_sensitive=case_sensitive,
            limit=limit,
        ),
    )


def matches_filters(
    file: dict[str, Any],
    *,
    path_prefix: str | None,
    language: str | None,
) -> bool:
    path = file.get("path")
    if not isinstance(path, str):
        return False
    if path_prefix is not None and path != path_prefix and not path.startswith(f"{path_prefix}/"):
        return False
    if language is not None and file.get("language") != language:
        return False
    return True


def file_skip_reason(file: dict[str, Any]) -> str | None:
    path = file.get("path")
    if not isinstance(path, str):
        return "missing_path"
    if is_unsafe_path(path):
        return "unsafe_path"
    size_bytes = file.get("size_bytes")
    if isinstance(size_bytes, int) and size_bytes > DEFAULT_SEARCH_FILE_BYTES:
        return "file_too_large"
    return None


def line_matches(text: str, query: str, *, case_sensitive: bool) -> bool:
    if case_sensitive:
        return query in text
    return query.lower() in text.lower()
