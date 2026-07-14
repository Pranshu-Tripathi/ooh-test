import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from ooh.db.models import (
    GuidanceSourceRead,
    GuidanceSourceType,
    RepoSnapshotRead,
    RepoSnapshotStatus,
    RepositoryRead,
    RepositorySourceType,
    RepositoryStatus,
)
from ooh.worker.context_pack_builder import ContextPackBuilder


def test_context_pack_builder_includes_bounded_code_and_guidance_excerpts(tmp_path: Path) -> None:
    repository_path = tmp_path / "repo"
    repository_path.mkdir()
    write_file(repository_path / "src" / "app.py", "def handle(value: str) -> str:\n    return value.upper()\n")
    write_file(repository_path / "AGENTS.md", "Use repository evidence when generating tests.\n")

    snapshot = build_snapshot(
        tmp_path,
        repository_path=repository_path,
        files=[
            snapshot_file(repository_path, "src/app.py"),
            snapshot_file(repository_path, "AGENTS.md"),
        ],
    )
    builder = ContextPackBuilder(cache_root=tmp_path / "cache")

    packs = builder.build(
        repository=build_repository(repository_path),
        snapshot=snapshot,
        drift_event=None,
        guidance_sources=[build_guidance_source("AGENTS.md", snapshot_file(repository_path, "AGENTS.md")["sha256"])],
        attention_profile=None,
        attention_focus_areas=[],
    )

    low_level_pack = next(pack for pack in packs if pack.pack_type.value == "low_level_components")
    payload = json.loads(Path(low_level_pack.artifact_uri).read_text(encoding="utf-8"))
    app_file = next(file for file in payload["included_files"] if file["path"] == "src/app.py")
    guidance = payload["included_guidance"][0]

    assert app_file["content_excerpt"]["omitted"] is False
    assert "def handle" in app_file["content_excerpt"]["text"]
    assert guidance["content_excerpt"]["omitted"] is False
    assert "repository evidence" in guidance["content_excerpt"]["text"]
    assert payload["prompt_content"]["included_excerpt_count"] >= 2


def test_context_pack_builder_omits_unsafe_prompt_files(tmp_path: Path) -> None:
    repository_path = tmp_path / "repo"
    repository_path.mkdir()
    write_file(repository_path / ".env", "API_TOKEN=do-not-ship\n")
    write_file(repository_path / "src" / "app.py", "TOKEN = 'not assignment syntax in env'\n")

    snapshot = build_snapshot(
        tmp_path,
        repository_path=repository_path,
        files=[
            snapshot_file(repository_path, ".env"),
            snapshot_file(repository_path, "src/app.py"),
        ],
    )
    builder = ContextPackBuilder(cache_root=tmp_path / "cache")

    packs = builder.build(
        repository=build_repository(repository_path),
        snapshot=snapshot,
        drift_event=None,
        guidance_sources=[],
        attention_profile=None,
        attention_focus_areas=[],
    )

    low_level_pack = next(pack for pack in packs if pack.pack_type.value == "low_level_components")
    payload = json.loads(Path(low_level_pack.artifact_uri).read_text(encoding="utf-8"))
    env_file = next(file for file in payload["included_files"] if file["path"] == ".env")
    app_file = next(file for file in payload["included_files"] if file["path"] == "src/app.py")

    assert env_file["content_excerpt"] == {"omitted": True, "omitted_reason": "unsafe_path"}
    assert "do-not-ship" not in json.dumps(payload)
    assert app_file["content_excerpt"]["omitted"] is False


def test_context_pack_builder_redacts_secret_looking_lines(tmp_path: Path) -> None:
    repository_path = tmp_path / "repo"
    repository_path.mkdir()
    write_file(
        repository_path / "src" / "settings.py",
        "SERVICE_URL = 'http://localhost'\nAPI_KEY = 'secret-value'\n",
    )
    snapshot = build_snapshot(
        tmp_path,
        repository_path=repository_path,
        files=[snapshot_file(repository_path, "src/settings.py")],
    )
    builder = ContextPackBuilder(cache_root=tmp_path / "cache")

    packs = builder.build(
        repository=build_repository(repository_path),
        snapshot=snapshot,
        drift_event=None,
        guidance_sources=[],
        attention_profile=None,
        attention_focus_areas=[],
    )

    low_level_pack = next(pack for pack in packs if pack.pack_type.value == "low_level_components")
    payload = json.loads(Path(low_level_pack.artifact_uri).read_text(encoding="utf-8"))
    settings_file = payload["included_files"][0]

    assert "SERVICE_URL" in settings_file["content_excerpt"]["text"]
    assert "secret-value" not in settings_file["content_excerpt"]["text"]
    assert "[redacted credential-looking line]" in settings_file["content_excerpt"]["text"]


def build_repository(repository_path: Path) -> RepositoryRead:
    now = datetime.now(UTC)
    return RepositoryRead(
        id=uuid4(),
        name="repo",
        source_type=RepositorySourceType.LOCAL_PATH,
        source_uri=str(repository_path),
        default_branch=None,
        token_ref=None,
        status=RepositoryStatus.INDEXED,
        last_processed_commit_sha=None,
        last_indexed_at=now,
        created_at=now,
        updated_at=now,
    )


def build_snapshot(
    tmp_path: Path,
    *,
    repository_path: Path,
    files: list[dict[str, object]],
) -> RepoSnapshotRead:
    repository_id = uuid4()
    snapshot_id = uuid4()
    snapshot_path = tmp_path / f"{snapshot_id}.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "repository_id": str(repository_id),
                "repository_name": "repo",
                "source_type": "local_path",
                "source_uri": str(repository_path),
                "resolved_path": str(repository_path),
                "commit_sha": "abc123",
                "files": files,
                "guidance_sources": [],
            }
        ),
        encoding="utf-8",
    )
    return RepoSnapshotRead(
        id=snapshot_id,
        repository_id=repository_id,
        commit_sha="abc123",
        index_uri=str(snapshot_path),
        status=RepoSnapshotStatus.COMPLETED,
        created_at=datetime.now(UTC),
    )


def build_guidance_source(path: str, content_hash: str) -> GuidanceSourceRead:
    now = datetime.now(UTC)
    return GuidanceSourceRead(
        id=uuid4(),
        repository_id=uuid4(),
        source_type=GuidanceSourceType.AGENTS,
        path=path,
        content_hash=content_hash,
        enabled=True,
        last_indexed_at=now,
        created_at=now,
        updated_at=now,
    )


def snapshot_file(repository_path: Path, relative_path: str) -> dict[str, object]:
    path = repository_path / relative_path
    raw = path.read_bytes()
    return {
        "path": relative_path,
        "size_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
