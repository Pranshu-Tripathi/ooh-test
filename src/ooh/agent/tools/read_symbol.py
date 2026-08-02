from __future__ import annotations

from typing import Any

from ooh.agent.tools.base import RepoTool, RepoToolArgs
from ooh.agent.tools.common import (
    DEFAULT_SYMBOL_READ_BYTES,
    RepoToolError,
    RepoToolResult,
    RepositoryToolContext,
    normalize_relative_path,
    read_source_excerpt,
    sort_symbols,
    symbol_summary,
)

TOOL_NAME = "repo.read_symbol"


class ReadSymbolArgs(RepoToolArgs):
    qualified_name: str | None = None
    name: str | None = None
    path: str | None = None


class ReadSymbolTool(RepoTool[ReadSymbolArgs]):
    name = TOOL_NAME
    description = "Read the bounded source range for one indexed symbol."
    args_model = ReadSymbolArgs

    def run(self, context: RepositoryToolContext, args: ReadSymbolArgs) -> RepoToolResult:
        symbol = resolve_symbol(
            context,
            qualified_name=args.qualified_name,
            name=args.name,
            path=args.path,
        )
        start_line = require_symbol_line(symbol, "start_line")
        end_line = require_symbol_line(symbol, "end_line")
        symbol_path = require_symbol_string(symbol, "path")
        symbol_qualified_name = require_symbol_string(symbol, "qualified_name")
        excerpt_payload, evidence_ref = read_source_excerpt(
            context,
            path=symbol_path,
            start_line=start_line,
            end_line=end_line,
            tool_name=self.name,
            max_bytes=DEFAULT_SYMBOL_READ_BYTES,
            qualified_name=symbol_qualified_name,
        )
        payload: dict[str, Any] = {
            "symbol": symbol_summary(symbol),
            "source": excerpt_payload,
        }
        return RepoToolResult(tool_name=self.name, payload=payload, evidence_refs=[evidence_ref])


TOOL = ReadSymbolTool()


def run(
    context: RepositoryToolContext,
    *,
    qualified_name: str | None = None,
    name: str | None = None,
    path: str | None = None,
) -> RepoToolResult:
    return TOOL.run(
        context,
        ReadSymbolArgs(
            qualified_name=qualified_name,
            name=name,
            path=path,
        ),
    )


def resolve_symbol(
    context: RepositoryToolContext,
    *,
    qualified_name: str | None,
    name: str | None,
    path: str | None,
) -> dict[str, Any]:
    normalized_path = normalize_relative_path(path) if path is not None else None
    query_qualified_name = stripped_optional(qualified_name)
    query_name = stripped_optional(name)
    if query_qualified_name is None and query_name is None:
        raise RepoToolError("qualified_name or name is required")

    matches = [
        symbol
        for symbol in sort_symbols(context.symbols)
        if matches_symbol_query(
            symbol,
            qualified_name=query_qualified_name,
            name=query_name,
            path=normalized_path,
        )
    ]
    if not matches:
        raise RepoToolError("symbol was not found")
    if len(matches) > 1:
        candidates = ", ".join(
            str(symbol.get("qualified_name"))
            for symbol in matches[:5]
        )
        raise RepoToolError(f"symbol query is ambiguous; candidates: {candidates}")
    return matches[0]


def matches_symbol_query(
    symbol: dict[str, Any],
    *,
    qualified_name: str | None,
    name: str | None,
    path: str | None,
) -> bool:
    if path is not None and symbol.get("path") != path:
        return False
    if qualified_name is not None:
        return symbol.get("qualified_name") == qualified_name
    return symbol.get("name") == name


def stripped_optional(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def require_symbol_line(symbol: dict[str, Any], key: str) -> int:
    value = symbol.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise RepoToolError(f"symbol is missing integer field: {key}")
    return value


def require_symbol_string(symbol: dict[str, Any], key: str) -> str:
    value = symbol.get(key)
    if not isinstance(value, str) or not value:
        raise RepoToolError(f"symbol is missing string field: {key}")
    return value
