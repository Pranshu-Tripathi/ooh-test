import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from ooh.agent.tools import (
    RepositoryToolContext,
    RepoToolError,
    ToolExecution,
    ToolExecutor,
    ToolInvocation,
    ToolTraceHandle,
    default_tool_registry,
)
from ooh.agent.tools import find_symbol as find_symbol_tool
from ooh.agent.tools import list_files as list_files_tool
from ooh.agent.tools import list_symbols as list_symbols_tool
from ooh.agent.tools import read_file_range as read_file_range_tool
from ooh.agent.tools import read_symbol as read_symbol_tool
from ooh.agent.tools import search_repo as search_repo_tool
from ooh.db.models import RepositoryRead, RepositorySourceType, RepositoryStatus
from ooh.worker.repository_inspector import LocalRepositoryInspector


def test_repo_tools_read_snapshot_files_and_symbols(tmp_path: Path) -> None:
    repository_path = build_repo(tmp_path)
    inspector = LocalRepositoryInspector(cache_root=tmp_path / "cache")
    snapshot = inspector.inspect_path(build_repository(repository_path), repository_path)
    context = RepositoryToolContext.from_index_uri(snapshot.index_uri, snapshot_id="snapshot-1")

    files_result = list_files_tool.run(context, language="python")
    assert files_result.tool_name == "repo.list_files"
    assert files_result.payload["total_matches"] == 2
    assert [file["path"] for file in files_result.payload["files"]] == [
        "src/app.py",
        "src/other.py",
    ]

    symbols_result = list_symbols_tool.run(context, path="src/app.py")
    assert [symbol["qualified_name"] for symbol in symbols_result.payload["symbols"]] == [
        "Service",
        "Service.handle",
        "helper",
    ]

    find_result = find_symbol_tool.run(context, query="handle")
    assert find_result.payload["symbols"][0]["qualified_name"] == "Service.handle"

    search_result = search_repo_tool.run(context, query="value.upper", language="python")
    assert search_result.payload["returned_matches"] == 1
    assert search_result.payload["results"][0]["path"] == "src/app.py"
    assert search_result.payload["results"][0]["line_number"] == 6
    assert search_result.evidence_refs[0]["source_uri"] == "code:src/app.py"
    assert search_result.evidence_refs[0]["metadata"]["start_line"] == 6

    read_result = read_symbol_tool.run(context, qualified_name="Service.handle")
    assert read_result.payload["symbol"]["kind"] == "method"
    assert "def handle(self, value: str) -> str:" in read_result.payload["source"]["content"]
    assert read_result.evidence_refs == [
        {
            "source_type": "code",
            "source_uri": "code:src/app.py",
            "content_hash": file_hash(snapshot.index_uri, "src/app.py"),
            "metadata": {
                "commit_sha": snapshot.commit_sha,
                "start_line": 5,
                "end_line": 6,
                "tool": "repo.read_symbol",
                "snapshot_id": "snapshot-1",
                "qualified_name": "Service.handle",
            },
        }
    ]


def test_read_file_range_returns_bounded_content_and_evidence(tmp_path: Path) -> None:
    repository_path = build_repo(tmp_path)
    inspector = LocalRepositoryInspector(cache_root=tmp_path / "cache")
    snapshot = inspector.inspect_path(build_repository(repository_path), repository_path)
    context = RepositoryToolContext.from_index_uri(snapshot.index_uri)

    result = read_file_range_tool.run(context, path="src/app.py", start_line=4, end_line=6)

    assert result.payload["path"] == "src/app.py"
    assert result.payload["start_line"] == 4
    assert result.payload["end_line"] == 6
    assert result.payload["lines"][0] == {"line_number": 4, "text": "class Service:"}
    assert result.payload["truncated"] is False
    assert result.evidence_refs[0]["source_uri"] == "code:src/app.py"
    assert result.evidence_refs[0]["metadata"]["start_line"] == 4
    assert result.evidence_refs[0]["metadata"]["end_line"] == 6


def test_repo_tools_reject_path_traversal_and_unsafe_files(tmp_path: Path) -> None:
    repository_path = build_repo(tmp_path)
    write_file(repository_path / ".env", "API_KEY=secret\n")
    inspector = LocalRepositoryInspector(cache_root=tmp_path / "cache")
    snapshot = inspector.inspect_path(build_repository(repository_path), repository_path)
    context = RepositoryToolContext.from_index_uri(snapshot.index_uri)

    with pytest.raises(RepoToolError, match="traversal"):
        read_file_range_tool.run(context, path="../src/app.py")

    with pytest.raises(RepoToolError, match="unsafe path"):
        read_file_range_tool.run(context, path=".env")


def test_read_symbol_requires_disambiguation_for_duplicate_names(tmp_path: Path) -> None:
    repository_path = build_repo(tmp_path)
    inspector = LocalRepositoryInspector(cache_root=tmp_path / "cache")
    snapshot = inspector.inspect_path(build_repository(repository_path), repository_path)
    context = RepositoryToolContext.from_index_uri(snapshot.index_uri)

    with pytest.raises(RepoToolError, match="ambiguous"):
        read_symbol_tool.run(context, name="helper")

    result = read_symbol_tool.run(context, name="helper", path="src/app.py")
    assert result.payload["symbol"]["qualified_name"] == "helper"


def test_tool_registry_exposes_class_contracts() -> None:
    registry = default_tool_registry()

    descriptors = registry.list()

    names = [descriptor.name for descriptor in descriptors]
    assert names == [
        "repo.find_symbol",
        "repo.list_files",
        "repo.list_symbols",
        "repo.read_file_range",
        "repo.read_symbol",
        "repo.search_repo",
    ]
    read_file_range = registry.get("repo.read_file_range")
    assert read_file_range.input_schema()["additionalProperties"] is False
    assert "path" in read_file_range.input_schema()["properties"]


def test_tool_registry_canonicalizes_implicit_defaults() -> None:
    registry = default_tool_registry()

    assert registry.canonical_arguments("repo.list_files", {}) == registry.canonical_arguments(
        "repo.list_files",
        {
            "path_prefix": None,
            "language": None,
            "parse_status": None,
            "limit": None,
        },
    )


def test_tool_executor_validates_arguments_and_records_success(tmp_path: Path) -> None:
    context = build_tool_context(tmp_path)
    recorder = RecordingToolTraceRecorder()
    executor = ToolExecutor(recorder=recorder)

    execution = executor.execute(
        tool_name="repo.find_symbol",
        context=context,
        arguments={"query": "handle"},
    )

    assert execution.result.payload["symbols"][0]["qualified_name"] == "Service.handle"
    assert recorder.started_invocations[0].tool_name == "repo.find_symbol"
    assert recorder.succeeded_executions == [execution]
    assert recorder.failed_errors == []


def test_tool_executor_records_failed_validation(tmp_path: Path) -> None:
    context = build_tool_context(tmp_path)
    recorder = RecordingToolTraceRecorder()
    executor = ToolExecutor(recorder=recorder)

    with pytest.raises(RepoToolError, match="invalid arguments"):
        executor.execute(
            tool_name="repo.find_symbol",
            context=context,
            arguments={"query": "handle", "unexpected": True},
        )

    assert recorder.started_invocations[0].tool_name == "repo.find_symbol"
    assert recorder.succeeded_executions == []
    assert len(recorder.failed_errors) == 1
    assert isinstance(recorder.failed_errors[0], RepoToolError)


class RecordingToolTraceRecorder:
    def __init__(self) -> None:
        self.started_invocations: list[ToolInvocation] = []
        self.succeeded_executions: list[ToolExecution] = []
        self.failed_errors: list[Exception] = []

    def started(self, invocation: ToolInvocation) -> ToolTraceHandle:
        self.started_invocations.append(invocation)
        return ToolTraceHandle()

    def succeeded(
        self,
        handle: ToolTraceHandle,
        execution: ToolExecution,
    ) -> None:
        self.succeeded_executions.append(execution)

    def failed(
        self,
        handle: ToolTraceHandle,
        invocation: ToolInvocation,
        error: Exception,
        duration_ms: int,
    ) -> None:
        self.failed_errors.append(error)


def build_tool_context(tmp_path: Path) -> RepositoryToolContext:
    repository_path = build_repo(tmp_path)
    inspector = LocalRepositoryInspector(cache_root=tmp_path / "cache")
    snapshot = inspector.inspect_path(build_repository(repository_path), repository_path)
    return RepositoryToolContext.from_index_uri(snapshot.index_uri)


def build_repo(tmp_path: Path) -> Path:
    repository_path = tmp_path / "repo"
    repository_path.mkdir()
    write_git_head(repository_path, "abc1234567890abc1234567890abc1234567890abc")
    write_file(
        repository_path / "src" / "app.py",
        "\n".join(
            [
                "import os",
                "from pathlib import Path",
                "",
                "class Service:",
                "    def handle(self, value: str) -> str:",
                "        return value.upper()",
                "",
                "def helper():",
                "    return Service()",
                "",
            ]
        ),
    )
    write_file(
        repository_path / "src" / "other.py",
        "\n".join(
            [
                "def helper():",
                "    return 'other'",
                "",
            ]
        ),
    )
    write_file(repository_path / "README.md", "Repository guidance.\n")
    return repository_path


def build_repository(repository_path: Path) -> RepositoryRead:
    now = datetime.now(UTC)
    return RepositoryRead(
        id=uuid4(),
        name="repo",
        source_type=RepositorySourceType.LOCAL_PATH,
        source_uri=str(repository_path),
        default_branch=None,
        token_ref=None,
        status=RepositoryStatus.PENDING,
        last_processed_commit_sha=None,
        last_indexed_at=None,
        created_at=now,
        updated_at=now,
    )


def file_hash(index_uri: str, path: str) -> str:
    manifest = json.loads(Path(index_uri).read_text(encoding="utf-8"))
    for file in manifest["files"]:
        if file["path"] == path:
            return file["sha256"]
    raise AssertionError(f"missing file hash for {path}")


def write_git_head(repository_path: Path, commit_sha: str) -> None:
    git_dir = repository_path / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text(commit_sha, encoding="utf-8")


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
