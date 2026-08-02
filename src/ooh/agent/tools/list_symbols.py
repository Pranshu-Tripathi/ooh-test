from __future__ import annotations

from typing import Any

from ooh.agent.tools.base import RepoTool, RepoToolArgs
from ooh.agent.tools.common import (
    DEFAULT_SYMBOL_LIMIT,
    RepoToolResult,
    RepositoryToolContext,
    bounded_limit,
    normalize_relative_path,
    sort_symbols,
    symbol_summary,
)

TOOL_NAME = "repo.list_symbols"


class ListSymbolsArgs(RepoToolArgs):
    path: str | None = None
    kind: str | None = None
    limit: int | None = None


class ListSymbolsTool(RepoTool[ListSymbolsArgs]):
    name = TOOL_NAME
    description = "List indexed symbols with optional file path and symbol-kind filters."
    args_model = ListSymbolsArgs

    def run(self, context: RepositoryToolContext, args: ListSymbolsArgs) -> RepoToolResult:
        max_results = bounded_limit(
            args.limit,
            default=DEFAULT_SYMBOL_LIMIT,
            maximum=DEFAULT_SYMBOL_LIMIT,
        )
        normalized_path = normalize_relative_path(args.path) if args.path is not None else None
        filtered_symbols = [
            symbol
            for symbol in sort_symbols(context.symbols)
            if matches_path(symbol, normalized_path) and matches_kind(symbol, args.kind)
        ]
        selected_symbols = filtered_symbols[:max_results]
        payload: dict[str, Any] = {
            "symbols": [symbol_summary(symbol) for symbol in selected_symbols],
            "total_matches": len(filtered_symbols),
            "truncated": len(filtered_symbols) > len(selected_symbols),
            "limit": max_results,
            "filters": {
                "path": normalized_path,
                "kind": args.kind,
            },
        }
        return RepoToolResult(tool_name=self.name, payload=payload, evidence_refs=[])


TOOL = ListSymbolsTool()


def run(
    context: RepositoryToolContext,
    *,
    path: str | None = None,
    kind: str | None = None,
    limit: int | None = None,
) -> RepoToolResult:
    return TOOL.run(context, ListSymbolsArgs(path=path, kind=kind, limit=limit))


def matches_path(symbol: dict[str, Any], path: str | None) -> bool:
    if path is None:
        return True
    return symbol.get("path") == path


def matches_kind(symbol: dict[str, Any], kind: str | None) -> bool:
    if kind is None:
        return True
    return symbol.get("kind") == kind
