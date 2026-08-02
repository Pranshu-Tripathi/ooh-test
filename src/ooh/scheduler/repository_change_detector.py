import subprocess
from dataclasses import dataclass
from pathlib import Path

from ooh.db.models import RepositoryRead, RepositorySourceType


@dataclass(frozen=True)
class RepositoryHead:
    commit_sha: str
    ref: str


class GitRepositoryChangeDetector:
    def __init__(self, *, command_timeout_seconds: float = 30) -> None:
        self.command_timeout_seconds = command_timeout_seconds

    def detect(self, repository: RepositoryRead) -> RepositoryHead:
        match repository.source_type:
            case RepositorySourceType.LOCAL_PATH:
                return self._detect_local(repository)
            case RepositorySourceType.GITHUB:
                return self._detect_remote(repository)

        raise ValueError(f"unsupported repository source type: {repository.source_type.value}")

    def _detect_local(self, repository: RepositoryRead) -> RepositoryHead:
        path = Path(repository.source_uri).expanduser().resolve()
        commit_sha = self._run(["git", "-C", str(path), "rev-parse", "HEAD"]).strip()
        if not commit_sha:
            raise RuntimeError("local repository HEAD did not resolve to a commit")
        return RepositoryHead(
            commit_sha=commit_sha,
            ref=repository.default_branch or "HEAD",
        )

    def _detect_remote(self, repository: RepositoryRead) -> RepositoryHead:
        if repository.token_ref is not None:
            raise ValueError("github token_ref is not wired to a secret provider yet")

        ref = (
            f"refs/heads/{repository.default_branch}"
            if repository.default_branch is not None
            else "HEAD"
        )
        output = self._run(["git", "ls-remote", repository.source_uri, ref])
        for line in output.splitlines():
            fields = line.split()
            if len(fields) == 2 and fields[1] == ref:
                return RepositoryHead(commit_sha=fields[0], ref=ref)
        raise RuntimeError(f"remote repository ref did not resolve to a commit: {ref}")

    def _run(self, command: list[str]) -> str:
        try:
            completed = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=self.command_timeout_seconds,
            )
            return completed.stdout
        except FileNotFoundError as exc:
            raise RuntimeError("git executable is not available") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("git command timed out") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout).strip()
            if detail:
                raise RuntimeError(f"git command failed: {detail}") from exc
            raise RuntimeError(f"git command failed with exit code {exc.returncode}") from exc
