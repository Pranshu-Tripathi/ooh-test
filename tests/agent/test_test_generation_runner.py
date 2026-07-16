import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from ooh.agent import AgentArtifactStore, GeneratedTestRunService
from ooh.agent.providers import ModelRequest, ModelResponse
from ooh.db.models import (
    AgentArtifactRead,
    AgentArtifactType,
    AgentRunRead,
    AgentRunType,
    AgentStatus,
    AgentStepRead,
    AgentStepType,
    ContextPackRead,
    ContextPackSourceRead,
    ContextPackSourceType,
    ContextPackType,
    GeneratedTestRead,
    ProvenanceRefRead,
    ProvenanceRefType,
    RepositoryRead,
    RepositorySourceType,
    RepositoryStatus,
)
from ooh.db.repos import (
    AgentArtifactInput,
    AgentRunInput,
    AgentStepInput,
    ContextPackWithSources,
    GeneratedTestInput,
    ProvenanceRefInput,
)
from ooh.worker.repository_inspector import LocalRepositoryInspector


class FakeProvider:
    def __init__(self, content: str | list[str]) -> None:
        self.contents = [content] if isinstance(content, str) else content
        self.requests: list[ModelRequest] = []

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self.contents) - 1)
        content = self.contents[index]
        return ModelResponse(
            model=request.model,
            content=content,
            raw_response={"message": {"content": content}},
            finish_reason="stop",
        )


class FakeGeneratedTestRepo:
    def __init__(self, *, fail_on_create: bool = False) -> None:
        self.inputs: list[GeneratedTestInput] = []
        self.fail_on_create = fail_on_create

    def create_many(self, inputs: list[GeneratedTestInput]) -> list[GeneratedTestRead]:
        if self.fail_on_create:
            raise RuntimeError("persist failed")
        self.inputs.extend(inputs)
        return [
            GeneratedTestRead(
                id=uuid4(),
                repository_id=input.repository_id,
                snapshot_id=input.snapshot_id,
                drift_event_id=input.drift_event_id,
                agent_run_id=input.agent_run_id,
                context_pack_id=input.context_pack_id,
                category=input.category,
                test_payload=input.test_payload,
                evidence_refs=input.evidence_refs,
                prompt_version=input.prompt_version,
                created_at=now(),
            )
            for input in inputs
        ]


class FakeAgentTraceRepo:
    def __init__(self) -> None:
        self.runs: dict[UUID, AgentRunRead] = {}
        self.steps: dict[UUID, AgentStepRead] = {}
        self.artifacts: list[AgentArtifactRead] = []
        self.provenance_refs: list[ProvenanceRefRead] = []

    def create_run(self, input: AgentRunInput) -> AgentRunRead:
        run = AgentRunRead(
            id=uuid4(),
            job_id=input.job_id,
            repository_id=input.repository_id,
            run_type=input.run_type,
            status=input.status,
            model_profile=input.model_profile,
            started_at=None,
            finished_at=None,
            created_at=now(),
        )
        self.runs[run.id] = run
        return run

    def mark_run_running(self, run_id: UUID) -> AgentRunRead:
        run = self.runs[run_id]
        updated = run.model_copy(update={"status": AgentStatus.RUNNING, "started_at": now()})
        self.runs[run_id] = updated
        return updated

    def mark_run_finished(self, run_id: UUID, *, status: AgentStatus) -> AgentRunRead:
        run = self.runs[run_id]
        updated = run.model_copy(update={"status": status, "finished_at": now()})
        self.runs[run_id] = updated
        return updated

    def create_step(self, input: AgentStepInput) -> AgentStepRead:
        step = AgentStepRead(
            id=uuid4(),
            agent_run_id=input.agent_run_id,
            step_type=input.step_type,
            status=input.status,
            sequence=input.sequence,
            input_summary=input.input_summary,
            output_summary=input.output_summary,
            warning_summary=input.warning_summary,
            started_at=None,
            finished_at=None,
            created_at=now(),
        )
        self.steps[step.id] = step
        return step

    def mark_step_running(self, step_id: UUID) -> AgentStepRead:
        step = self.steps[step_id]
        updated = step.model_copy(update={"status": AgentStatus.RUNNING, "started_at": now()})
        self.steps[step_id] = updated
        return updated

    def mark_step_finished(
        self,
        step_id: UUID,
        *,
        status: AgentStatus,
        output_summary: dict[str, Any] | None = None,
        warning_summary: list[dict[str, Any]] | None = None,
    ) -> AgentStepRead:
        step = self.steps[step_id]
        updated = step.model_copy(
            update={
                "status": status,
                "output_summary": output_summary if output_summary is not None else step.output_summary,
                "warning_summary": warning_summary if warning_summary is not None else step.warning_summary,
                "finished_at": now(),
            }
        )
        self.steps[step_id] = updated
        return updated

    def create_artifact(self, input: AgentArtifactInput) -> AgentArtifactRead:
        artifact = AgentArtifactRead(
            id=uuid4(),
            agent_step_id=input.agent_step_id,
            artifact_type=input.artifact_type,
            artifact_uri=input.artifact_uri,
            content_hash=input.content_hash,
            created_at=now(),
        )
        self.artifacts.append(artifact)
        return artifact

    def create_provenance_ref(self, input: ProvenanceRefInput) -> ProvenanceRefRead:
        provenance_ref = ProvenanceRefRead(
            id=uuid4(),
            artifact_id=input.artifact_id,
            ref_type=input.ref_type,
            ref_uri=input.ref_uri,
            content_hash=input.content_hash,
            metadata=input.metadata,
            created_at=now(),
        )
        self.provenance_refs.append(provenance_ref)
        return provenance_ref


def test_generated_test_run_service_persists_tests_and_trace_artifacts(tmp_path) -> None:
    provider = FakeProvider(
        json.dumps(
            {
                "type": "short_answer",
                "question": "What should the developer inspect?",
                "expected_answer": "Inspect src/app.py and AGENTS.md.",
                "evidence_refs": [
                    {
                        "source_type": "code",
                        "source_uri": "code:src/app.py",
                        "content_hash": "code-hash",
                    }
                ],
            }
        )
    )
    trace_repo = FakeAgentTraceRepo()
    generated_test_repo = FakeGeneratedTestRepo()
    service = GeneratedTestRunService(
        provider=provider,
        model="qwen3-coder:8b",
        artifact_store=AgentArtifactStore(cache_root=tmp_path),
        agent_trace_repo=trace_repo,
        generated_test_repo=generated_test_repo,
    )

    context_pack = build_context_pack(tmp_path)
    result = service.generate_for_context_packs([context_pack], job_id=uuid4())

    assert result.agent_run.status == AgentStatus.SUCCEEDED
    assert result.agent_run.run_type == AgentRunType.TEST_GENERATION
    assert len(result.generated_tests) == 1
    assert generated_test_repo.inputs[0].agent_run_id == result.agent_run.id
    assert generated_test_repo.inputs[0].context_pack_id == context_pack.context_pack.id
    assert generated_test_repo.inputs[0].test_payload["type"] == "short_answer"
    assert generated_test_repo.inputs[0].drift_event_id == context_pack_drift_id()

    assert [step.step_type for step in trace_repo.steps.values()] == [
        AgentStepType.GENERATE_QUESTIONS,
        AgentStepType.PERSIST_RESULT,
    ]
    assert all(step.status == AgentStatus.SUCCEEDED for step in trace_repo.steps.values())
    assert [artifact.artifact_type for artifact in trace_repo.artifacts] == [
        AgentArtifactType.PROMPT,
        AgentArtifactType.RAW_MODEL_RESPONSE,
        AgentArtifactType.VALIDATED_OUTPUT,
    ]
    for artifact in trace_repo.artifacts:
        assert artifact.content_hash is not None
        assert artifact.artifact_uri.startswith(str(tmp_path))

    provenance_types = [provenance.ref_type for provenance in trace_repo.provenance_refs]
    assert provenance_types == [
        ProvenanceRefType.CONTEXT_PACK,
        ProvenanceRefType.PROMPT,
        ProvenanceRefType.MODEL,
        ProvenanceRefType.CODE,
        ProvenanceRefType.GUIDANCE,
    ]

    assert len(provider.requests) == 1
    assert provider.requests[0].response_format == "json_object"


def test_generated_test_run_service_inspects_context_pack_with_repo_tools(tmp_path: Path) -> None:
    provider = FakeProvider(
        json.dumps(
            {
                "type": "short_answer",
                "question": "Which method uppercases the value?",
                "expected_answer": "Service.handle uppercases the value.",
                "evidence_refs": [{"source_type": "code", "source_uri": "code:src/app.py"}],
            }
        )
    )
    trace_repo = FakeAgentTraceRepo()
    generated_test_repo = FakeGeneratedTestRepo()
    service = GeneratedTestRunService(
        provider=provider,
        model="qwen3-coder:8b",
        artifact_store=AgentArtifactStore(cache_root=tmp_path / "artifacts"),
        agent_trace_repo=trace_repo,
        generated_test_repo=generated_test_repo,
    )

    context_pack = build_context_pack_with_snapshot(tmp_path)

    result = service.generate_for_context_packs([context_pack], job_id=uuid4())

    assert result.agent_run.status == AgentStatus.SUCCEEDED
    assert generated_test_repo.inputs[0].test_payload["evidence_refs"][0]["source_uri"] == (
        "code:src/app.py"
    )

    steps_by_sequence = sorted(trace_repo.steps.values(), key=lambda step: step.sequence)
    assert [step.step_type for step in steps_by_sequence] == [
        AgentStepType.TOOL_CALL,
        AgentStepType.TOOL_CALL,
        AgentStepType.TOOL_CALL,
        AgentStepType.GENERATE_QUESTIONS,
        AgentStepType.PERSIST_RESULT,
    ]
    assert all(step.status == AgentStatus.SUCCEEDED for step in steps_by_sequence)
    tool_steps = [step for step in steps_by_sequence if step.step_type == AgentStepType.TOOL_CALL]
    assert [step.input_summary["tool_name"] for step in tool_steps] == [
        "repo.list_files",
        "repo.list_symbols",
        "repo.read_file_range",
    ]

    artifact_types = [artifact.artifact_type for artifact in trace_repo.artifacts]
    assert artifact_types.count(AgentArtifactType.TOOL_CALL_RESULT) == 3
    assert AgentArtifactType.PROMPT in artifact_types
    assert ProvenanceRefType.CODE in [ref.ref_type for ref in trace_repo.provenance_refs]

    prompt = provider.requests[0].messages[1].content
    assert "tool_inspection" in prompt
    assert "Service.handle" in prompt
    assert "return value.upper()" in prompt


def test_generated_test_run_service_marks_run_failed_on_invalid_model_output(tmp_path) -> None:
    trace_repo = FakeAgentTraceRepo()
    service = GeneratedTestRunService(
        provider=FakeProvider('{"type": "short_answer", "question": "Missing answer"}'),
        model="qwen3-coder:8b",
        artifact_store=AgentArtifactStore(cache_root=tmp_path),
        agent_trace_repo=trace_repo,
        generated_test_repo=FakeGeneratedTestRepo(),
    )

    with pytest.raises(Exception, match="generated test contract"):
        service.generate_for_context_packs([build_context_pack(tmp_path)])

    assert list(trace_repo.runs.values())[0].status == AgentStatus.FAILED
    assert any(step.status == AgentStatus.FAILED for step in trace_repo.steps.values())
    assert [artifact.artifact_type for artifact in trace_repo.artifacts] == [
        AgentArtifactType.PROMPT,
        AgentArtifactType.RAW_MODEL_RESPONSE,
        AgentArtifactType.TRACE,
        AgentArtifactType.PROMPT,
        AgentArtifactType.RAW_MODEL_RESPONSE,
        AgentArtifactType.TRACE,
    ]
    artifact_names = [artifact.artifact_uri.rsplit("/", maxsplit=1)[-1] for artifact in trace_repo.artifacts]
    assert any("turn-1-generate-validation-error" in name for name in artifact_names)
    assert any("turn-2-repair-validation-error" in name for name in artifact_names)


def test_generated_test_run_service_records_repair_turn_artifacts(tmp_path) -> None:
    trace_repo = FakeAgentTraceRepo()
    generated_test_repo = FakeGeneratedTestRepo()
    service = GeneratedTestRunService(
        provider=FakeProvider(
            [
                '{"type": "short_answer", "question": "Missing answer"}',
                json.dumps(
                    {
                        "type": "short_answer",
                        "question": "What matters?",
                        "expected_answer": "The evidence matters.",
                        "evidence_refs": [{"source_type": "code", "source_uri": "code:src/app.py"}],
                    }
                ),
            ]
        ),
        model="qwen3-coder:8b",
        artifact_store=AgentArtifactStore(cache_root=tmp_path),
        agent_trace_repo=trace_repo,
        generated_test_repo=generated_test_repo,
    )

    result = service.generate_for_context_packs([build_context_pack(tmp_path)])

    assert result.agent_run.status == AgentStatus.SUCCEEDED
    assert [request.metadata.get("repair") for request in service.provider.requests] == [None, True]
    assert [artifact.artifact_type for artifact in trace_repo.artifacts] == [
        AgentArtifactType.PROMPT,
        AgentArtifactType.RAW_MODEL_RESPONSE,
        AgentArtifactType.TRACE,
        AgentArtifactType.PROMPT,
        AgentArtifactType.RAW_MODEL_RESPONSE,
        AgentArtifactType.VALIDATED_OUTPUT,
    ]
    artifact_names = [artifact.artifact_uri.rsplit("/", maxsplit=1)[-1] for artifact in trace_repo.artifacts]
    assert any("turn-1-generate-validation-error" in name for name in artifact_names)
    assert any("turn-2-repair-prompt" in name for name in artifact_names)


def test_generated_test_run_service_records_evidence_regeneration_artifacts(tmp_path) -> None:
    trace_repo = FakeAgentTraceRepo()
    generated_test_repo = FakeGeneratedTestRepo()
    service = GeneratedTestRunService(
        provider=FakeProvider(
            [
                json.dumps(
                    {
                        "type": "short_answer",
                        "question": "What matters?",
                        "expected_answer": "The missing file matters.",
                        "evidence_refs": [{"source_type": "code", "source_uri": "code:missing.py"}],
                    }
                ),
                json.dumps(
                    {
                        "type": "short_answer",
                        "question": "What matters?",
                        "expected_answer": "src/app.py matters.",
                        "evidence_refs": [{"source_type": "code", "source_uri": "code:src/app.py"}],
                    }
                ),
            ]
        ),
        model="qwen3-coder:8b",
        artifact_store=AgentArtifactStore(cache_root=tmp_path),
        agent_trace_repo=trace_repo,
        generated_test_repo=generated_test_repo,
    )

    result = service.generate_for_context_packs([build_context_pack(tmp_path)])

    assert result.agent_run.status == AgentStatus.SUCCEEDED
    assert [request.metadata.get("evidence_feedback") for request in service.provider.requests] == [
        None,
        True,
    ]
    assert generated_test_repo.inputs[0].evidence_refs == [
        {
            "source_type": "code",
            "source_uri": "code:src/app.py",
            "content_hash": "code-hash",
            "metadata": {},
        }
    ]
    assert [artifact.artifact_type for artifact in trace_repo.artifacts] == [
        AgentArtifactType.PROMPT,
        AgentArtifactType.RAW_MODEL_RESPONSE,
        AgentArtifactType.TRACE,
        AgentArtifactType.PROMPT,
        AgentArtifactType.RAW_MODEL_RESPONSE,
        AgentArtifactType.VALIDATED_OUTPUT,
    ]
    artifact_names = [artifact.artifact_uri.rsplit("/", maxsplit=1)[-1] for artifact in trace_repo.artifacts]
    assert any("turn-1-generate-evidence-error" in name for name in artifact_names)
    assert any("turn-2-regenerate_evidence-prompt" in name for name in artifact_names)


def test_generated_test_run_service_does_not_fail_generation_step_when_persist_fails(tmp_path) -> None:
    trace_repo = FakeAgentTraceRepo()
    service = GeneratedTestRunService(
        provider=FakeProvider(
            json.dumps(
                {
                    "type": "short_answer",
                    "question": "What matters?",
                    "expected_answer": "The evidence matters.",
                    "evidence_refs": [{"source_type": "code", "source_uri": "code:src/app.py"}],
                }
            )
        ),
        model="qwen3-coder:8b",
        artifact_store=AgentArtifactStore(cache_root=tmp_path),
        agent_trace_repo=trace_repo,
        generated_test_repo=FakeGeneratedTestRepo(fail_on_create=True),
    )

    with pytest.raises(RuntimeError, match="persist failed"):
        service.generate_for_context_packs([build_context_pack(tmp_path)])

    steps_by_type = {step.step_type: step for step in trace_repo.steps.values()}
    assert steps_by_type[AgentStepType.GENERATE_QUESTIONS].status == AgentStatus.SUCCEEDED
    assert steps_by_type[AgentStepType.PERSIST_RESULT].status == AgentStatus.FAILED
    assert list(trace_repo.runs.values())[0].status == AgentStatus.FAILED


def test_generated_test_run_service_rejects_empty_context_packs(tmp_path) -> None:
    service = GeneratedTestRunService(
        provider=FakeProvider("{}"),
        model="qwen3-coder:8b",
        artifact_store=AgentArtifactStore(cache_root=tmp_path),
        agent_trace_repo=FakeAgentTraceRepo(),
        generated_test_repo=FakeGeneratedTestRepo(),
    )

    with pytest.raises(ValueError, match="at least one context pack"):
        service.generate_for_context_packs([])


def build_context_pack(tmp_path) -> ContextPackWithSources:
    repository_id = uuid4()
    snapshot_id = uuid4()
    context_pack_id = uuid4()
    artifact_path = tmp_path / "context-pack.json"
    artifact_path.write_text(
        json.dumps(
            {
                "pack_type": "active_pr",
                "drift": {"id": str(context_pack_drift_id())},
                "source_refs": [
                    {
                        "source_type": "code",
                        "source_uri": "code:src/app.py",
                        "content_hash": "code-hash",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return ContextPackWithSources(
        context_pack=ContextPackRead(
            id=context_pack_id,
            repository_id=repository_id,
            snapshot_id=snapshot_id,
            attention_profile_id=None,
            pack_type=ContextPackType.ACTIVE_PR,
            artifact_uri=str(artifact_path),
            content_hash="context-pack-hash",
            created_at=now(),
        ),
        sources=[
            ContextPackSourceRead(
                id=uuid4(),
                context_pack_id=context_pack_id,
                source_type=ContextPackSourceType.CODE,
                source_uri="code:src/app.py",
                content_hash="code-hash",
                created_at=now(),
            ),
            ContextPackSourceRead(
                id=uuid4(),
                context_pack_id=context_pack_id,
                source_type=ContextPackSourceType.GUIDANCE,
                source_uri="guidance:AGENTS.md",
                content_hash="guidance-hash",
                created_at=now(),
            ),
            ContextPackSourceRead(
                id=uuid4(),
                context_pack_id=context_pack_id,
                source_type=ContextPackSourceType.DRIFT,
                source_uri=f"drift_event:{context_pack_drift_id()}",
                content_hash=None,
                created_at=now(),
            ),
        ],
    )


def build_context_pack_with_snapshot(tmp_path: Path) -> ContextPackWithSources:
    repository_path = tmp_path / "repo"
    repository_path.mkdir()
    write_git_head(repository_path, "abc1234567890abc1234567890abc1234567890abc")
    write_file(
        repository_path / "src" / "app.py",
        "\n".join(
            [
                "class Service:",
                "    def handle(self, value: str) -> str:",
                "        return value.upper()",
                "",
            ]
        ),
    )

    inspector = LocalRepositoryInspector(cache_root=tmp_path / "cache")
    snapshot = inspector.inspect_path(build_repository(repository_path), repository_path)
    snapshot_id_value = uuid4()
    repository_id = uuid4()
    context_pack_id = uuid4()
    code_hash = snapshot_file_hash(snapshot.index_uri, "src/app.py")
    artifact_path = tmp_path / "context-pack-with-snapshot.json"
    artifact_path.write_text(
        json.dumps(
            {
                "pack_type": "low_level_components",
                "snapshot": {
                    "id": str(snapshot_id_value),
                    "commit_sha": snapshot.commit_sha,
                    "index_uri": snapshot.index_uri,
                },
                "included_files": [
                    {
                        "path": "src/app.py",
                        "sha256": code_hash,
                        "language": "python",
                        "parse_status": "parsed",
                    }
                ],
                "source_refs": [
                    {
                        "source_type": "code",
                        "source_uri": "code:src/app.py",
                        "content_hash": code_hash,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return ContextPackWithSources(
        context_pack=ContextPackRead(
            id=context_pack_id,
            repository_id=repository_id,
            snapshot_id=snapshot_id_value,
            attention_profile_id=None,
            pack_type=ContextPackType.LOW_LEVEL_COMPONENTS,
            artifact_uri=str(artifact_path),
            content_hash="context-pack-hash",
            created_at=now(),
        ),
        sources=[
            ContextPackSourceRead(
                id=uuid4(),
                context_pack_id=context_pack_id,
                source_type=ContextPackSourceType.CODE,
                source_uri="code:src/app.py",
                content_hash=code_hash,
                created_at=now(),
            )
        ],
    )


def build_repository(repository_path: Path) -> RepositoryRead:
    current_time = now()
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
        created_at=current_time,
        updated_at=current_time,
    )


def snapshot_file_hash(index_uri: str, path: str) -> str:
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


def context_pack_drift_id() -> UUID:
    return UUID("00000000-0000-4000-8000-000000000001")


def now() -> datetime:
    return datetime.now(UTC)
