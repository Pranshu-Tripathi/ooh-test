from pathlib import Path

import pytest

from ooh.agent.loop_runtime import AgentLoopDeadlineExceeded, LoopDeadline
from ooh.agent.providers import (
    ModelProvider,
    ModelProviderError,
    ModelRequest,
    ModelResponse,
    ModelToolCall,
)
from ooh.agent.repository_inspection import ModelDirectedRepositoryInspector
from ooh.agent.tools import RepositoryToolContext, ToolExecutor


class QueuedProvider:
    def __init__(self, responses: list[ModelResponse]) -> None:
        self.responses = responses
        self.requests: list[ModelRequest] = []

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return self.responses[len(self.requests) - 1]


class ManyCallProvider:
    def __init__(self, call_count: int) -> None:
        self.call_count = call_count
        self.requests: list[ModelRequest] = []

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        turn = len(self.requests)
        if turn > self.call_count:
            return response(content="Inspection complete")
        return response(
            tool_calls=[
                ModelToolCall(
                    name="repo.list_files",
                    arguments={"path_prefix": f"missing-{turn}"},
                    call_id=f"files-{turn}",
                )
            ]
        )


class MutableClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


def test_repository_inspector_executes_novel_calls_and_rejects_canonical_duplicates(
    tmp_path: Path,
) -> None:
    provider = QueuedProvider(
        [
            response(
                tool_calls=[
                    ModelToolCall(name="repo.list_files", arguments={}, call_id="files-1")
                ]
            ),
            response(
                tool_calls=[
                    ModelToolCall(
                        name="repo.list_files",
                        arguments={
                            "path_prefix": None,
                            "language": None,
                            "parse_status": None,
                            "limit": None,
                        },
                        call_id="files-duplicate",
                    ),
                    ModelToolCall(
                        name="repo.list_files",
                        arguments={"path_prefix": "src"},
                        call_id="files-src",
                    ),
                ]
            ),
            response(content="Inspection complete"),
        ]
    )
    inspector = build_inspector(provider)

    result = inspector.run(
        context_pack=context_pack(),
        tool_context=tool_context(tmp_path),
        deadline=LoopDeadline.start(600),
    )

    assert result.completion_reason == "model_finished"
    assert len(result.turns) == 3
    assert [execution.invocation.call_id for execution in result.executions] == [
        "files-1",
        "files-src",
    ]
    assert [duplicate.call_id for duplicate in result.duplicate_calls] == ["files-duplicate"]
    assert result.failed_calls == []
    assert all(request.tools for request in provider.requests)
    assert all(request.timeout_seconds is not None for request in provider.requests)
    assert all(
        request.metadata["prompt_bytes"] <= request.metadata["prompt_max_bytes"]
        for request in provider.requests
    )
    assert "files-1" in provider.requests[1].messages[1].content
    assert "files-duplicate" in provider.requests[2].messages[1].content


def test_repository_inspector_has_no_tool_call_count_limit(tmp_path: Path) -> None:
    provider = ManyCallProvider(call_count=12)
    inspector = build_inspector(provider)

    result = inspector.run(
        context_pack=context_pack(),
        tool_context=tool_context(tmp_path),
        deadline=LoopDeadline.start(600),
    )

    assert len(result.executions) == 12
    assert len(result.turns) == 13
    assert result.duplicate_calls == []


def test_repository_inspector_rejects_repeated_invalid_call(tmp_path: Path) -> None:
    invalid_call = ModelToolCall(
        name="repo.read_file_range",
        arguments={"path": "src/app.py", "unexpected": True},
    )
    provider = QueuedProvider(
        [
            response(tool_calls=[invalid_call]),
            response(tool_calls=[invalid_call]),
            response(content="Inspection complete"),
        ]
    )
    inspector = build_inspector(provider)

    result = inspector.run(
        context_pack=context_pack(),
        tool_context=tool_context(tmp_path),
        deadline=LoopDeadline.start(600),
    )

    assert len(result.failed_calls) == 1
    assert len(result.duplicate_calls) == 1
    assert result.executions == []


def test_repository_inspector_enforces_shared_deadline_after_model_call(tmp_path: Path) -> None:
    clock = MutableClock()

    class SlowProvider:
        def __init__(self) -> None:
            self.requests: list[ModelRequest] = []

        def generate(self, request: ModelRequest) -> ModelResponse:
            self.requests.append(request)
            clock.value = 11
            return response(content="Inspection complete")

    provider = SlowProvider()
    inspector = build_inspector(provider)

    with pytest.raises(AgentLoopDeadlineExceeded, match="processing repository inspection"):
        inspector.run(
            context_pack=context_pack(),
            tool_context=tool_context(tmp_path),
            deadline=LoopDeadline.start(10, clock=clock),
        )

    assert provider.requests[0].timeout_seconds == 10


def test_repository_inspector_propagates_terminal_provider_failure(tmp_path: Path) -> None:
    class FailingProvider:
        def generate(self, request: ModelRequest) -> ModelResponse:
            raise ModelProviderError("model unavailable")

    inspector = build_inspector(FailingProvider())

    with pytest.raises(ModelProviderError, match="model unavailable"):
        inspector.run(
            context_pack=context_pack(),
            tool_context=tool_context(tmp_path),
            deadline=LoopDeadline.start(600),
        )


def build_inspector(provider: ModelProvider) -> ModelDirectedRepositoryInspector:
    return ModelDirectedRepositoryInspector(
        provider=provider,
        model="qwen3:8b",
        tool_executor=ToolExecutor(),
    )


def response(
    *,
    content: str = "",
    tool_calls: list[ModelToolCall] | None = None,
) -> ModelResponse:
    return ModelResponse(
        model="qwen3:8b",
        content=content,
        raw_response={"message": {"content": content}},
        finish_reason="stop",
        tool_calls=tool_calls or [],
    )


def context_pack() -> dict[str, object]:
    return {
        "schema_version": 1,
        "pack_type": "low_level_components",
        "repository": {"name": "repo", "source_type": "local_path"},
        "snapshot": {"commit_sha": "abc123"},
        "source_refs": [],
        "included_files": [],
        "included_guidance": [],
    }


def tool_context(tmp_path: Path) -> RepositoryToolContext:
    repository_path = tmp_path / "repo"
    repository_path.mkdir(exist_ok=True)
    return RepositoryToolContext(
        index_uri=str(tmp_path / "index.json"),
        repository_id="repository-1",
        commit_sha="abc123",
        repository_path=repository_path,
        files=[],
        symbols=[],
        imports=[],
        snapshot_id="snapshot-1",
    )
