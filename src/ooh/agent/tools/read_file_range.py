from __future__ import annotations

from ooh.agent.tools.base import RepoTool, RepoToolArgs
from ooh.agent.tools.common import (
    DEFAULT_READ_FILE_RANGE_BYTES,
    DEFAULT_READ_FILE_RANGE_LINES,
    RepoToolResult,
    RepositoryToolContext,
    read_source_excerpt,
)

TOOL_NAME = "repo.read_file_range"


class ReadFileRangeArgs(RepoToolArgs):
    path: str
    start_line: int = 1
    end_line: int | None = None
    max_lines: int = DEFAULT_READ_FILE_RANGE_LINES
    max_bytes: int = DEFAULT_READ_FILE_RANGE_BYTES


class ReadFileRangeTool(RepoTool[ReadFileRangeArgs]):
    name = TOOL_NAME
    description = "Read a bounded 1-based inclusive line range from an indexed repository file."
    args_model = ReadFileRangeArgs

    def run(self, context: RepositoryToolContext, args: ReadFileRangeArgs) -> RepoToolResult:
        requested_end_line = (
            args.end_line
            if args.end_line is not None
            else args.start_line + args.max_lines - 1
        )
        payload, evidence_ref = read_source_excerpt(
            context,
            path=args.path,
            start_line=args.start_line,
            end_line=requested_end_line,
            tool_name=self.name,
            max_lines=args.max_lines,
            max_bytes=args.max_bytes,
        )
        return RepoToolResult(tool_name=self.name, payload=payload, evidence_refs=[evidence_ref])


TOOL = ReadFileRangeTool()


def run(
    context: RepositoryToolContext,
    *,
    path: str,
    start_line: int = 1,
    end_line: int | None = None,
    max_lines: int = DEFAULT_READ_FILE_RANGE_LINES,
    max_bytes: int = DEFAULT_READ_FILE_RANGE_BYTES,
) -> RepoToolResult:
    return TOOL.run(
        context,
        ReadFileRangeArgs(
            path=path,
            start_line=start_line,
            end_line=end_line,
            max_lines=max_lines,
            max_bytes=max_bytes,
        ),
    )
