from __future__ import annotations

import fnmatch
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ooh.db.models import GuidanceSourceType, RepositoryRead, RepositorySourceType

ALWAYS_IGNORED_DIR_NAMES = {
    ".git",
}

GUIDANCE_FILE_NAME_TYPES = {
    "agents.md": GuidanceSourceType.AGENTS,
    "claude.md": GuidanceSourceType.CLAUDE,
    "readme.md": GuidanceSourceType.README,
}

GUIDANCE_DIR_TYPES = {
    ".cursor": GuidanceSourceType.CURSOR,
    "adr": GuidanceSourceType.ADR,
    "adrs": GuidanceSourceType.ADR,
    "design": GuidanceSourceType.DESIGN,
    "docs": GuidanceSourceType.DOCS,
}


@dataclass(frozen=True)
class FileSnapshot:
    path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class GitIgnoreRule:
    pattern: str
    directory_only: bool
    anchored: bool
    negated: bool

    def matches(self, relative_path: str, *, is_dir: bool) -> bool:
        if self.directory_only and not is_dir:
            return False

        path = relative_path.rstrip("/")
        if not path:
            return False

        if "/" not in self.pattern:
            return any(fnmatch.fnmatchcase(part, self.pattern) for part in path.split("/"))

        if self.anchored:
            return fnmatch.fnmatchcase(path, self.pattern)

        return fnmatch.fnmatchcase(path, self.pattern) or fnmatch.fnmatchcase(path, f"*/{self.pattern}")


@dataclass(frozen=True)
class GuidanceSourceSnapshot:
    source_type: GuidanceSourceType
    path: str
    content_hash: str


@dataclass(frozen=True)
class LocalRepositorySnapshot:
    commit_sha: str
    index_uri: str
    file_count: int
    total_bytes: int
    guidance_sources: list[GuidanceSourceSnapshot]


class LocalRepositoryInspector:
    def __init__(self, *, cache_root: Path) -> None:
        self.cache_root = cache_root

    def inspect(self, repository: RepositoryRead) -> LocalRepositorySnapshot:
        if repository.source_type != RepositorySourceType.LOCAL_PATH:
            raise ValueError(f"unsupported repository source type: {repository.source_type.value}")

        repository_path = Path(repository.source_uri).expanduser().resolve()
        if not repository_path.exists():
            raise ValueError(f"repository path is not visible to worker: {repository_path}")
        if not repository_path.is_dir():
            raise ValueError(f"repository path is not a directory: {repository_path}")

        git_dir = self._git_dir(repository_path)
        commit_sha = self._head_commit_sha(git_dir)
        files = self._snapshot_files(repository_path)
        guidance_sources = self._guidance_sources(files)
        index_uri = self._write_snapshot(
            repository=repository,
            repository_path=repository_path,
            commit_sha=commit_sha,
            files=files,
            guidance_sources=guidance_sources,
        )

        return LocalRepositorySnapshot(
            commit_sha=commit_sha,
            index_uri=index_uri,
            file_count=len(files),
            total_bytes=sum(file.size_bytes for file in files),
            guidance_sources=guidance_sources,
        )

    def _snapshot_files(self, repository_path: Path) -> list[FileSnapshot]:
        files: list[FileSnapshot] = []
        gitignore_rules = self._root_gitignore_rules(repository_path)

        for directory, dir_names, file_names in repository_path.walk():
            dir_names[:] = sorted(
                name
                for name in dir_names
                if not self._is_ignored_dir(repository_path, directory / name, gitignore_rules)
            )

            for file_name in sorted(file_names):
                path = directory / file_name
                if path.is_symlink():
                    continue
                if not path.is_file():
                    continue
                if self._is_ignored_path(repository_path, path, gitignore_rules, is_dir=False):
                    continue

                files.append(
                    FileSnapshot(
                        path=path.relative_to(repository_path).as_posix(),
                        size_bytes=path.stat().st_size,
                        sha256=self._sha256(path),
                    )
                )

        return files

    def _guidance_sources(self, files: list[FileSnapshot]) -> list[GuidanceSourceSnapshot]:
        guidance_sources: list[GuidanceSourceSnapshot] = []

        for file in files:
            path = Path(file.path)
            source_type = GUIDANCE_FILE_NAME_TYPES.get(path.name.lower())
            if source_type is None:
                source_type = self._guidance_type_for_path(path)
            if source_type is None:
                continue

            guidance_sources.append(
                GuidanceSourceSnapshot(
                    source_type=source_type,
                    path=file.path,
                    content_hash=file.sha256,
                )
            )

        return guidance_sources

    def _write_snapshot(
        self,
        *,
        repository: RepositoryRead,
        repository_path: Path,
        commit_sha: str,
        files: list[FileSnapshot],
        guidance_sources: list[GuidanceSourceSnapshot],
    ) -> str:
        snapshot_dir = self.cache_root / "repositories" / str(repository.id) / "snapshots" / commit_sha
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        snapshot_path = snapshot_dir / "snapshot.json"

        payload = {
            "schema_version": 1,
            "repository_id": str(repository.id),
            "repository_name": repository.name,
            "source_type": repository.source_type.value,
            "source_uri": repository.source_uri,
            "resolved_path": str(repository_path),
            "commit_sha": commit_sha,
            "generated_at": datetime.now(UTC).isoformat(),
            "file_count": len(files),
            "total_bytes": sum(file.size_bytes for file in files),
            "files": [
                {"path": file.path, "size_bytes": file.size_bytes, "sha256": file.sha256}
                for file in files
            ],
            "guidance_sources": [
                {
                    "source_type": guidance.source_type.value,
                    "path": guidance.path,
                    "content_hash": guidance.content_hash,
                }
                for guidance in guidance_sources
            ],
        }
        snapshot_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return str(snapshot_path)

    @staticmethod
    def _git_dir(repository_path: Path) -> Path:
        git_path = repository_path / ".git"
        if git_path.is_dir():
            return git_path
        if git_path.is_file():
            content = git_path.read_text(encoding="utf-8").strip()
            prefix = "gitdir: "
            if content.startswith(prefix):
                return (repository_path / content[len(prefix) :]).resolve()
        raise ValueError(f"repository path is not a git repository: {repository_path}")

    @staticmethod
    def _head_commit_sha(git_dir: Path) -> str:
        head_path = git_dir / "HEAD"
        head = head_path.read_text(encoding="utf-8").strip()
        if not head.startswith("ref: "):
            return head

        ref = head.removeprefix("ref: ").strip()
        ref_path = git_dir / ref
        if ref_path.exists():
            return ref_path.read_text(encoding="utf-8").strip()

        packed_refs_path = git_dir / "packed-refs"
        if packed_refs_path.exists():
            for line in packed_refs_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("#") or not line.strip():
                    continue
                commit_sha, packed_ref = line.split(" ", maxsplit=1)
                if packed_ref == ref:
                    return commit_sha

        raise ValueError(f"unable to resolve git ref: {ref}")

    @staticmethod
    def _guidance_type_for_path(path: Path) -> GuidanceSourceType | None:
        lowered_parts = [part.lower() for part in path.parts]
        for part in lowered_parts[:-1]:
            if part in GUIDANCE_DIR_TYPES:
                return GUIDANCE_DIR_TYPES[part]
        return None

    @staticmethod
    def _is_ignored_dir(
        repository_path: Path,
        path: Path,
        gitignore_rules: list[GitIgnoreRule],
    ) -> bool:
        if path.name in ALWAYS_IGNORED_DIR_NAMES or path.is_symlink():
            return True
        return LocalRepositoryInspector._is_ignored_path(
            repository_path,
            path,
            gitignore_rules,
            is_dir=True,
        )

    @staticmethod
    def _is_ignored_path(
        repository_path: Path,
        path: Path,
        gitignore_rules: list[GitIgnoreRule],
        *,
        is_dir: bool,
    ) -> bool:
        relative_path = path.relative_to(repository_path).as_posix()
        ignored = False
        for rule in gitignore_rules:
            if rule.matches(relative_path, is_dir=is_dir):
                ignored = not rule.negated
        return ignored

    @staticmethod
    def _root_gitignore_rules(repository_path: Path) -> list[GitIgnoreRule]:
        gitignore_path = repository_path / ".gitignore"
        if not gitignore_path.exists():
            return []

        rules: list[GitIgnoreRule] = []
        for raw_line in gitignore_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            negated = line.startswith("!")
            if negated:
                line = line[1:]

            directory_only = line.endswith("/")
            line = line.rstrip("/")
            anchored = line.startswith("/")
            line = line.lstrip("/")
            if not line:
                continue

            rules.append(
                GitIgnoreRule(
                    pattern=line,
                    directory_only=directory_only,
                    anchored=anchored,
                    negated=negated,
                )
            )
        return rules

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
