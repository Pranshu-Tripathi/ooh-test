import fnmatch
import subprocess
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from ooh.db.models import DriftSeverity

CONFIG_FILE_NAMES = {
    "dockerfile",
    "compose.yaml",
    "compose.yml",
    "docker-compose.yaml",
    "docker-compose.yml",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "pyproject.toml",
    "poetry.lock",
    "requirements.txt",
    "alembic.ini",
}

CONFIG_PATH_PARTS = {
    ".github",
    "alembic",
    "migrations",
}


@dataclass(frozen=True)
class DriftSummary:
    from_commit_sha: str | None
    to_commit_sha: str
    drift_score: Decimal
    severity: DriftSeverity
    breakdown: dict[str, Any]
    should_record: bool


@dataclass(frozen=True)
class FileChange:
    path: str
    status: str
    additions: int
    deletions: int
    old_path: str | None = None


@dataclass(frozen=True)
class AttentionFocusWeight:
    name: str
    weight: Decimal
    path_globs: list[str]


@dataclass(frozen=True)
class AttentionProfileWeights:
    name: str
    default_weight: Decimal
    focus_areas: list[AttentionFocusWeight]


class GitDriftScorer:
    def score(
        self,
        repository_path: Path,
        *,
        from_commit_sha: str | None,
        to_commit_sha: str,
        attention_profile: AttentionProfileWeights | None = None,
    ) -> DriftSummary:
        if from_commit_sha is None:
            return DriftSummary(
                from_commit_sha=None,
                to_commit_sha=to_commit_sha,
                drift_score=Decimal("0.00"),
                severity=DriftSeverity.LOW,
                breakdown={
                    "baseline": True,
                    "changed_file_count": 0,
                    "line_additions": 0,
                    "line_deletions": 0,
                    "deleted_file_count": 0,
                    "renamed_file_count": 0,
                    "config_file_count": 0,
                    "changed_files": [],
                    "attention_profile": self._attention_profile_breakdown(attention_profile),
                },
                should_record=True,
            )

        if from_commit_sha == to_commit_sha:
            return DriftSummary(
                from_commit_sha=from_commit_sha,
                to_commit_sha=to_commit_sha,
                drift_score=Decimal("0.00"),
                severity=DriftSeverity.LOW,
                breakdown={"unchanged": True},
                should_record=False,
            )

        changes = self._changes(repository_path, from_commit_sha=from_commit_sha, to_commit_sha=to_commit_sha)
        additions = sum(change.additions for change in changes)
        deletions = sum(change.deletions for change in changes)
        deleted_count = sum(1 for change in changes if change.status.startswith("D"))
        renamed_count = sum(1 for change in changes if change.status.startswith("R"))
        config_changes = [change for change in changes if self._is_config_path(change.path)]
        weighted_changes = [
            (
                change,
                *self._change_attention(change, attention_profile),
                self._base_change_score(change),
            )
            for change in changes
        ]
        raw_score = sum(
            base_score * weight for _change, weight, _matched_focus_areas, base_score in weighted_changes
        )
        drift_score = raw_score.quantize(Decimal("0.01"))

        return DriftSummary(
            from_commit_sha=from_commit_sha,
            to_commit_sha=to_commit_sha,
            drift_score=drift_score,
            severity=self._severity(drift_score),
            breakdown={
                "baseline": False,
                "changed_file_count": len(changes),
                "line_additions": additions,
                "line_deletions": deletions,
                "deleted_file_count": deleted_count,
                "renamed_file_count": renamed_count,
                "config_file_count": len(config_changes),
                "attention_profile": self._attention_profile_breakdown(attention_profile),
                "changed_files": [
                    {
                        "path": change.path,
                        "old_path": change.old_path,
                        "status": change.status,
                        "additions": change.additions,
                        "deletions": change.deletions,
                        "config": self._is_config_path(change.path),
                        "attention_weight": str(weight),
                        "matched_focus_areas": matched_focus_areas,
                        "base_score": str(base_score.quantize(Decimal("0.01"))),
                    }
                    for change, weight, matched_focus_areas, base_score in weighted_changes[:100]
                ],
                "changed_files_truncated": len(changes) > 100,
            },
            should_record=True,
        )

    def _changes(self, repository_path: Path, *, from_commit_sha: str, to_commit_sha: str) -> list[FileChange]:
        numstat_by_path = self._numstat_by_path(repository_path, from_commit_sha, to_commit_sha)
        changes: list[FileChange] = []

        for line in self._git(
            repository_path,
            ["diff", "--name-status", "--find-renames", from_commit_sha, to_commit_sha],
        ).splitlines():
            if not line.strip():
                continue
            parts = line.split("\t")
            status = parts[0]
            old_path = None
            path = parts[-1]
            if status.startswith("R") and len(parts) == 3:
                old_path = parts[1]

            additions, deletions = numstat_by_path.get(path, (0, 0))
            changes.append(
                FileChange(
                    path=path,
                    old_path=old_path,
                    status=status,
                    additions=additions,
                    deletions=deletions,
                )
            )

        return changes

    def _numstat_by_path(
        self,
        repository_path: Path,
        from_commit_sha: str,
        to_commit_sha: str,
    ) -> dict[str, tuple[int, int]]:
        stats: dict[str, tuple[int, int]] = {}
        for line in self._git(
            repository_path,
            ["diff", "--numstat", "--find-renames", from_commit_sha, to_commit_sha],
        ).splitlines():
            if not line.strip():
                continue
            additions, deletions, *paths = line.split("\t")
            path = paths[-1]
            stats[path] = (self._parse_numstat_count(additions), self._parse_numstat_count(deletions))
        return stats

    @staticmethod
    def _git(repository_path: Path, args: list[str]) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(repository_path), *args],
                check=True,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("git executable is not available") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout).strip()
            if detail:
                raise RuntimeError(f"git command failed: {detail}") from exc
            raise RuntimeError(f"git command failed with exit code {exc.returncode}") from exc
        return result.stdout

    @staticmethod
    def _is_config_path(path: str) -> bool:
        parsed_path = Path(path)
        if parsed_path.name.lower() in CONFIG_FILE_NAMES:
            return True
        return any(part.lower() in CONFIG_PATH_PARTS for part in parsed_path.parts)

    @classmethod
    def _base_change_score(cls, change: FileChange) -> Decimal:
        score = Decimal("2.0") + Decimal(change.additions + change.deletions) / Decimal(25)
        if change.status.startswith("D"):
            score += Decimal("5.0")
        if change.status.startswith("R"):
            score += Decimal("3.0")
        if cls._is_config_path(change.path):
            score += Decimal("10.0")
        return score

    @staticmethod
    def _change_attention(
        change: FileChange,
        attention_profile: AttentionProfileWeights | None,
    ) -> tuple[Decimal, list[str]]:
        if attention_profile is None:
            return Decimal("1.0"), []

        candidate_paths = [change.path]
        if change.old_path is not None:
            candidate_paths.append(change.old_path)

        matched_focus_areas = [
            focus_area
            for focus_area in attention_profile.focus_areas
            if any(
                fnmatch.fnmatchcase(path, path_glob)
                for path in candidate_paths
                for path_glob in focus_area.path_globs
            )
        ]
        if not matched_focus_areas:
            return attention_profile.default_weight, []

        strongest_focus_area = max(matched_focus_areas, key=lambda focus_area: focus_area.weight)
        return strongest_focus_area.weight, [focus_area.name for focus_area in matched_focus_areas]

    @staticmethod
    def _attention_profile_breakdown(
        attention_profile: AttentionProfileWeights | None,
    ) -> dict[str, Any] | None:
        if attention_profile is None:
            return None
        return {
            "name": attention_profile.name,
            "default_weight": str(attention_profile.default_weight),
            "focus_areas": [
                {
                    "name": focus_area.name,
                    "weight": str(focus_area.weight),
                    "path_globs": focus_area.path_globs,
                }
                for focus_area in attention_profile.focus_areas
            ],
        }

    @staticmethod
    def _parse_numstat_count(value: str) -> int:
        if value == "-":
            return 0
        return int(value)

    @staticmethod
    def _severity(drift_score: Decimal) -> DriftSeverity:
        if drift_score >= Decimal("80"):
            return DriftSeverity.HIGH
        if drift_score >= Decimal("25"):
            return DriftSeverity.MEDIUM
        return DriftSeverity.LOW
