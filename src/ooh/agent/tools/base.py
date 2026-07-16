from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError

from ooh.agent.tools.common import RepoToolError, RepoToolResult, RepositoryToolContext


class RepoToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


ArgsT = TypeVar("ArgsT", bound=RepoToolArgs)


class RepoTool(ABC, Generic[ArgsT]):
    name: ClassVar[str]
    description: ClassVar[str]
    args_model: ClassVar[type[ArgsT]]

    def input_schema(self) -> dict[str, Any]:
        return self.args_model.model_json_schema()

    def run_json(
        self,
        context: RepositoryToolContext,
        raw_args: dict[str, Any],
    ) -> RepoToolResult:
        return self.run(context, self.validate_args(raw_args))

    def validate_args(self, raw_args: dict[str, Any]) -> ArgsT:
        try:
            return self.args_model.model_validate(raw_args)
        except ValidationError as exc:
            raise RepoToolError(f"invalid arguments for {self.name}: {exc}") from exc

    @abstractmethod
    def run(self, context: RepositoryToolContext, args: ArgsT) -> RepoToolResult:
        pass
