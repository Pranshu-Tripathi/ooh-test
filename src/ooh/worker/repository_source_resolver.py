import subprocess
from dataclasses import dataclass
from pathlib import Path

from ooh.db.models import RepositoryRead, RepositorySourceType


@dataclass(frozen=True)
class ResolvedRepositorySource:
    path: Path


class RepositorySourceResolver:
    def __init__(self, *, cache_root: Path) -> None:
        self.cache_root = cache_root

    def resolve(
        self,
        repository: RepositoryRead,
        *,
        target_commit_sha: str | None = None,
    ) -> ResolvedRepositorySource:
        match repository.source_type:
            case RepositorySourceType.LOCAL_PATH:
                return self._resolve_local_path(
                    repository,
                    target_commit_sha=target_commit_sha,
                )
            case RepositorySourceType.GITHUB:
                return self._resolve_github(
                    repository,
                    target_commit_sha=target_commit_sha,
                )

        raise ValueError(f"unsupported repository source type: {repository.source_type.value}")

    def _resolve_local_path(
        self,
        repository: RepositoryRead,
        *,
        target_commit_sha: str | None,
    ) -> ResolvedRepositorySource:
        source_path = Path(repository.source_uri).expanduser().resolve()
        target_commit_sha = target_commit_sha or self._run(
            ["git", "-C", str(source_path), "rev-parse", "HEAD"]
        ).strip()
        if not target_commit_sha:
            raise RuntimeError("local repository HEAD did not resolve to a commit")
        return self._resolve_exact_checkout(
            repository,
            source_uri=str(source_path),
            target_commit_sha=target_commit_sha,
        )

    def _resolve_github(
        self,
        repository: RepositoryRead,
        *,
        target_commit_sha: str | None,
    ) -> ResolvedRepositorySource:
        if repository.token_ref is not None:
            raise ValueError("github token_ref is not wired to a secret provider yet")

        if target_commit_sha is not None:
            return self._resolve_exact_checkout(
                repository,
                source_uri=repository.source_uri,
                target_commit_sha=target_commit_sha,
            )

        checkout_path = self.cache_root / "repositories" / str(repository.id) / "checkout"
        if self._is_git_checkout(checkout_path):
            self._update_checkout(checkout_path, repository)
            return ResolvedRepositorySource(path=checkout_path)

        if checkout_path.exists() and any(checkout_path.iterdir()):
            raise ValueError(f"cached checkout path exists but is not a git repository: {checkout_path}")

        checkout_path.parent.mkdir(parents=True, exist_ok=True)
        self._clone(repository, checkout_path)
        return ResolvedRepositorySource(path=checkout_path)

    def _resolve_exact_checkout(
        self,
        repository: RepositoryRead,
        *,
        source_uri: str,
        target_commit_sha: str,
    ) -> ResolvedRepositorySource:
        checkout_path = self.cache_root / "repositories" / str(repository.id) / "checkout"
        if self._is_git_checkout(checkout_path):
            self._run(
                [
                    "git",
                    "-C",
                    str(checkout_path),
                    "remote",
                    "set-url",
                    "origin",
                    source_uri,
                ]
            )
            self._run(["git", "-C", str(checkout_path), "fetch", "--prune", "origin"])
        else:
            if checkout_path.exists() and any(checkout_path.iterdir()):
                raise ValueError(
                    f"cached checkout path exists but is not a git repository: {checkout_path}"
                )
            checkout_path.parent.mkdir(parents=True, exist_ok=True)
            self._run(["git", "clone", "--no-checkout", source_uri, str(checkout_path)])

        self._run(
            [
                "git",
                "-C",
                str(checkout_path),
                "checkout",
                "--detach",
                target_commit_sha,
            ]
        )
        return ResolvedRepositorySource(path=checkout_path)

    def _clone(self, repository: RepositoryRead, checkout_path: Path) -> None:
        command = ["git", "clone"]
        if repository.default_branch is not None:
            command.extend(["--branch", repository.default_branch])
        command.extend([repository.source_uri, str(checkout_path)])
        self._run(command)

    def _update_checkout(self, checkout_path: Path, repository: RepositoryRead) -> None:
        self._run(["git", "-C", str(checkout_path), "remote", "set-url", "origin", repository.source_uri])
        self._run(["git", "-C", str(checkout_path), "fetch", "--prune", "origin"])

        if repository.default_branch is not None:
            self._run(["git", "-C", str(checkout_path), "checkout", repository.default_branch])
            self._run(
                ["git", "-C", str(checkout_path), "pull", "--ff-only", "origin", repository.default_branch]
            )
            return

        self._run(["git", "-C", str(checkout_path), "pull", "--ff-only"])

    @staticmethod
    def _is_git_checkout(path: Path) -> bool:
        return (path / ".git").exists()

    @staticmethod
    def _run(command: list[str]) -> str:
        try:
            completed = subprocess.run(command, check=True, capture_output=True, text=True)
            return completed.stdout
        except FileNotFoundError as exc:
            raise RuntimeError("git executable is not available") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout).strip()
            if detail:
                raise RuntimeError(f"git command failed: {detail}") from exc
            raise RuntimeError(f"git command failed with exit code {exc.returncode}") from exc
