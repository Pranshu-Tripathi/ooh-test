import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from ooh.db.models import RepositoryRead, RepositorySourceType, RepositoryStatus
from ooh.scheduler.repository_change_detector import GitRepositoryChangeDetector


def test_local_detector_reads_committed_head_and_ignores_working_tree(tmp_path: Path) -> None:
    repository_path = tmp_path / "repository"
    repository_path.mkdir()
    initialize_repository(repository_path)
    commit_sha = commit_file(repository_path, "tracked.txt", "committed\n")
    (repository_path / "tracked.txt").write_text("uncommitted\n", encoding="utf-8")

    head = GitRepositoryChangeDetector().detect(
        build_repository(
            source_type=RepositorySourceType.LOCAL_PATH,
            source_uri=str(repository_path),
        )
    )

    assert head.commit_sha == commit_sha
    assert head.ref == "HEAD"


def test_remote_detector_reads_configured_branch_tip() -> None:
    detector = GitRepositoryChangeDetector()
    commands: list[list[str]] = []

    def fake_run(command: list[str]) -> str:
        commands.append(command)
        return "abc123\trefs/heads/develop\n"

    detector._run = fake_run  # type: ignore[method-assign]

    head = detector.detect(
        build_repository(
            source_type=RepositorySourceType.GITHUB,
            source_uri="https://github.com/example/project.git",
            default_branch="develop",
        )
    )

    assert head.commit_sha == "abc123"
    assert head.ref == "refs/heads/develop"
    assert commands == [
        [
            "git",
            "ls-remote",
            "https://github.com/example/project.git",
            "refs/heads/develop",
        ]
    ]


def build_repository(
    *,
    source_type: RepositorySourceType,
    source_uri: str,
    default_branch: str | None = None,
) -> RepositoryRead:
    now = datetime.now(UTC)
    return RepositoryRead(
        id=uuid4(),
        name="repository",
        source_type=source_type,
        source_uri=source_uri,
        default_branch=default_branch,
        token_ref=None,
        status=RepositoryStatus.INDEXED,
        last_processed_commit_sha=None,
        last_indexed_at=None,
        created_at=now,
        updated_at=now,
    )


def initialize_repository(path: Path) -> None:
    run_git(path, "init")
    run_git(path, "config", "user.email", "tests@example.com")
    run_git(path, "config", "user.name", "Tests")


def commit_file(path: Path, filename: str, content: str) -> str:
    (path / filename).write_text(content, encoding="utf-8")
    run_git(path, "add", filename)
    run_git(path, "commit", "-m", f"update {filename}")
    return run_git(path, "rev-parse", "HEAD").strip()


def run_git(path: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(path), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout
