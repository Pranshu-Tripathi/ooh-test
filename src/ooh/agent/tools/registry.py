from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ooh.agent.tools.base import RepoTool
from ooh.agent.tools.common import RepoToolError
from ooh.agent.tools.find_symbol import FindSymbolTool
from ooh.agent.tools.list_files import ListFilesTool
from ooh.agent.tools.list_symbols import ListSymbolsTool
from ooh.agent.tools.read_file_range import ReadFileRangeTool
from ooh.agent.tools.read_symbol import ReadSymbolTool
from ooh.agent.tools.search_repo import SearchRepoTool


@dataclass(frozen=True)
class ToolDescriptor:
    name: str
    description: str
    input_schema: dict[str, object]


class ToolRegistry:
    def __init__(self, tools: list[RepoTool[Any]]) -> None:
        tools_by_name: dict[str, RepoTool[Any]] = {}
        for tool in tools:
            if tool.name in tools_by_name:
                raise RepoToolError(f"duplicate tool name: {tool.name}")
            tools_by_name[tool.name] = tool
        self._tools_by_name = tools_by_name

    def get(self, name: str) -> RepoTool[Any]:
        tool = self._tools_by_name.get(name)
        if tool is None:
            raise RepoToolError(f"unknown tool: {name}")
        return tool

    def list(self) -> list[ToolDescriptor]:
        return [
            ToolDescriptor(
                name=tool.name,
                description=tool.description,
                input_schema=tool.input_schema(),
            )
            for tool in sorted(self._tools_by_name.values(), key=lambda item: item.name)
        ]

    def canonical_arguments(self, name: str, raw_args: dict[str, Any]) -> dict[str, Any]:
        validated = self.get(name).validate_args(raw_args)
        return validated.model_dump(mode="json")


def default_tool_registry() -> ToolRegistry:
    return ToolRegistry(
        [
            FindSymbolTool(),
            ListFilesTool(),
            ListSymbolsTool(),
            ReadFileRangeTool(),
            ReadSymbolTool(),
            SearchRepoTool(),
        ]
    )
