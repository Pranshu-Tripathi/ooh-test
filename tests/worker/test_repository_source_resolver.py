import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from ooh.db.models import RepositoryRead, RepositorySourceType, RepositoryStatus
from ooh.worker.repository_source_resolver import RepositorySourceResolver


def test_local_resolver_uses_exact_committed_tree_and_not_working_tree(tmp_path: Path) -> None:
    source_path = tmp_path / "source"
    source_path.mkdir()
    initialize_repository(source_path)
    first_sha = commit_file(source_path, "tracked.txt", "first\n")
    (source_path / "tracked.txt").write_text("uncommitted\n", encoding="utf-8")

    resolver = RepositorySourceResolver(cache_root=tmp_path / "cache")
    repository = build_repository(source_path)

    first_checkout = resolver.resolve(repository)

    assert first_checkout.path != source_path
    assert (first_checkout.path / "tracked.txt").read_text(encoding="utf-8") == "first\n"
    assert run_git(first_checkout.path, "rev-parse", "HEAD").strip() == first_sha

    second_sha = commit_file(source_path, "tracked.txt", "second\n")
    old_checkout = resolver.resolve(repository, target_commit_sha=first_sha)

    assert second_sha != first_sha
    assert run_git(old_checkout.path, "rev-parse", "HEAD").strip() == first_sha
    assert (old_checkout.path / "tracked.txt").read_text(encoding="utf-8") == "first\n"


def build_repository(source_path: Path) -> RepositoryRead:
    now = datetime.now(UTC)
    return RepositoryRead(
        id=uuid4(),
        name="repository",
        source_type=RepositorySourceType.LOCAL_PATH,
        source_uri=str(source_path),
        default_branch=None,
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
