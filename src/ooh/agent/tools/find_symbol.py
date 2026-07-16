from __future__ import annotations

from typing import Any

from ooh.agent.tools.base import RepoTool, RepoToolArgs
from ooh.agent.tools.common import (
    DEFAULT_SYMBOL_LIMIT,
    RepoToolError,
    RepoToolResult,
    RepositoryToolContext,
    bounded_limit,
    sort_symbols,
    symbol_summary,
)

TOOL_NAME = "repo.find_symbol"


class FindSymbolArgs(RepoToolArgs):
    query: str
    limit: int | None = None


class FindSymbolTool(RepoTool[FindSymbolArgs]):
    name = TOOL_NAME
    description = "Find indexed symbols by case-insensitive name, qualified name, or path substring."
    args_model = FindSymbolArgs

    def run(self, context: RepositoryToolContext, args: FindSymbolArgs) -> RepoToolResult:
        stripped_query = args.query.strip()
        if not stripped_query:
            raise RepoToolError("query must not be blank")

        max_results = bounded_limit(
            args.limit,
            default=DEFAULT_SYMBOL_LIMIT,
            maximum=DEFAULT_SYMBOL_LIMIT,
        )
        matched_symbols = [
            symbol
            for symbol in context.symbols
            if symbol_matches(symbol, stripped_query)
        ]
        ranked_symbols = sorted(matched_symbols, key=lambda symbol: symbol_rank(symbol, stripped_query))
        selected_symbols = ranked_symbols[:max_results]
        payload: dict[str, Any] = {
            "query": stripped_query,
            "symbols": [symbol_summary(symbol) for symbol in selected_symbols],
            "total_matches": len(matched_symbols),
            "truncated": len(matched_symbols) > len(selected_symbols),
            "limit": max_results,
        }
        return RepoToolResult(tool_name=self.name, payload=payload, evidence_refs=[])


TOOL = FindSymbolTool()


def run(
    context: RepositoryToolContext,
    *,
    query: str,
    limit: int | None = None,
) -> RepoToolResult:
    return TOOL.run(context, FindSymbolArgs(query=query, limit=limit))


def symbol_matches(symbol: dict[str, Any], query: str) -> bool:
    lowered_query = query.lower()
    fields = [
        symbol.get("qualified_name"),
        symbol.get("name"),
        symbol.get("path"),
    ]
    return any(
        isinstance(field, str) and lowered_query in field.lower()
        for field in fields
    )


def symbol_rank(symbol: dict[str, Any], query: str) -> tuple[int, str, int, str]:
    lowered_query = query.lower()
    qualified_name = str(symbol.get("qualified_name", ""))
    name = str(symbol.get("name", ""))
    lowered_qualified_name = qualified_name.lower()
    lowered_name = name.lower()

    if lowered_qualified_name == lowered_query:
        exactness = 0
    elif lowered_name == lowered_query:
        exactness = 1
    elif lowered_qualified_name.startswith(lowered_query):
        exactness = 2
    elif lowered_name.startswith(lowered_query):
        exactness = 3
    else:
        exactness = 4

    sorted_symbol = sort_symbols([symbol])[0]
    return (
        exactness,
        str(sorted_symbol.get("path", "")),
        int(sorted_symbol.get("start_line", 0) or 0),
        qualified_name,
    )
