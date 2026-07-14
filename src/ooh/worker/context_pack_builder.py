from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from ooh.db.models import (
    AttentionFocusAreaRead,
    AttentionProfileRead,
    ContextPackSourceType,
    ContextPackType,
    DriftEventRead,
    GuidanceSourceRead,
    RepoSnapshotRead,
    RepositoryRead,
)
from ooh.db.repos import ContextPackSourceInput

DEFAULT_MAX_PACK_CONTENT_BYTES = 60_000
DEFAULT_MAX_CODE_EXCERPT_BYTES = 12_000
DEFAULT_MAX_GUIDANCE_EXCERPT_BYTES = 8_000

UNSAFE_PROMPT_PATH_NAMES = {
    ".env",
    ".env.local",
    ".envrc",
    ".npmrc",
    ".pypirc",
    "credentials",
    "credentials.json",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
}

UNSAFE_PROMPT_EXTENSIONS = {
    ".crt",
    ".der",
    ".key",
    ".pem",
    ".pfx",
    ".p12",
}

BINARY_PROMPT_EXTENSIONS = {
    ".7z",
    ".avif",
    ".bin",
    ".bmp",
    ".class",
    ".dll",
    ".dylib",
    ".exe",
    ".gif",
    ".ico",
    ".jar",
    ".jpeg",
    ".jpg",
    ".lockb",
    ".pdf",
    ".png",
    ".pyc",
    ".so",
    ".webp",
    ".zip",
}

SECRET_LINE_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|auth[_-]?token|client[_-]?secret|password|private[_-]?key|secret|token)\b"
    r"\s*[:=]"
)


@dataclass(frozen=True)
class BuiltContextPack:
    pack_type: ContextPackType
    artifact_uri: str
    content_hash: str
    sources: list[ContextPackSourceInput]


@dataclass
class PromptContentBudget:
    max_bytes: int
    used_bytes: int = 0
    included_excerpt_count: int = 0
    omitted_excerpt_count: int = 0

    @property
    def remaining_bytes(self) -> int:
        return max(self.max_bytes - self.used_bytes, 0)

    def consume(self, text: str) -> None:
        self.used_bytes += len(text.encode("utf-8"))
        self.included_excerpt_count += 1

    def omit(self) -> None:
        self.omitted_excerpt_count += 1


class ContextPackBuilder:
    def __init__(
        self,
        *,
        cache_root: Path,
        max_pack_content_bytes: int = DEFAULT_MAX_PACK_CONTENT_BYTES,
        max_code_excerpt_bytes: int = DEFAULT_MAX_CODE_EXCERPT_BYTES,
        max_guidance_excerpt_bytes: int = DEFAULT_MAX_GUIDANCE_EXCERPT_BYTES,
    ) -> None:
        self.cache_root = cache_root
        self.max_pack_content_bytes = max_pack_content_bytes
        self.max_code_excerpt_bytes = max_code_excerpt_bytes
        self.max_guidance_excerpt_bytes = max_guidance_excerpt_bytes

    def build(
        self,
        *,
        repository: RepositoryRead,
        snapshot: RepoSnapshotRead,
        drift_event: DriftEventRead | None,
        guidance_sources: list[GuidanceSourceRead],
        attention_profile: AttentionProfileRead | None,
        attention_focus_areas: list[AttentionFocusAreaRead],
    ) -> list[BuiltContextPack]:
        snapshot_payload = self._read_snapshot(snapshot)
        changed_files = self._changed_files(snapshot_payload, drift_event)
        guidance_by_path = {source.path: source for source in guidance_sources}

        packs = [
            self._pack_payload(
                pack_type=ContextPackType.HIGH_LEVEL_DESIGN,
                snapshot_payload=snapshot_payload,
                repository=repository,
                snapshot=snapshot,
                drift_event=drift_event,
                guidance_sources=guidance_sources,
                changed_files=changed_files,
                attention_profile=attention_profile,
                attention_focus_areas=attention_focus_areas,
                files=self._high_level_files(snapshot_payload, changed_files),
                included_guidance=[
                    source
                    for source in guidance_sources
                    if source.source_type.value in {"readme", "docs", "adr", "design"}
                ],
                purpose="System shape, architecture, config, and design tradeoffs.",
            ),
            self._pack_payload(
                pack_type=ContextPackType.LOW_LEVEL_COMPONENTS,
                snapshot_payload=snapshot_payload,
                repository=repository,
                snapshot=snapshot,
                drift_event=drift_event,
                guidance_sources=guidance_sources,
                changed_files=changed_files,
                attention_profile=attention_profile,
                attention_focus_areas=attention_focus_areas,
                files=self._low_level_files(snapshot_payload, changed_files),
                included_guidance=list(guidance_by_path.values())[:10],
                purpose="Concrete changed files and nearby implementation details.",
            ),
            self._pack_payload(
                pack_type=ContextPackType.DESIGN_DECISIONS,
                snapshot_payload=snapshot_payload,
                repository=repository,
                snapshot=snapshot,
                drift_event=drift_event,
                guidance_sources=guidance_sources,
                changed_files=changed_files,
                attention_profile=attention_profile,
                attention_focus_areas=attention_focus_areas,
                files=self._design_decision_files(snapshot_payload, changed_files),
                included_guidance=[
                    source
                    for source in guidance_sources
                    if source.source_type.value in {"agents", "claude", "adr", "design"}
                ],
                purpose="Rules, ADRs, and design assumptions that should shape questions.",
            ),
            self._pack_payload(
                pack_type=ContextPackType.FUTURE_IMPROVEMENTS,
                snapshot_payload=snapshot_payload,
                repository=repository,
                snapshot=snapshot,
                drift_event=drift_event,
                guidance_sources=guidance_sources,
                changed_files=changed_files,
                attention_profile=attention_profile,
                attention_focus_areas=attention_focus_areas,
                files=self._future_improvement_files(snapshot_payload, changed_files),
                included_guidance=list(guidance_by_path.values())[:10],
                purpose="Risk signals, repeated churn, deletes, renames, and improvement prompts.",
            ),
            self._pack_payload(
                pack_type=ContextPackType.ACTIVE_PR,
                snapshot_payload=snapshot_payload,
                repository=repository,
                snapshot=snapshot,
                drift_event=drift_event,
                guidance_sources=guidance_sources,
                changed_files=changed_files,
                attention_profile=attention_profile,
                attention_focus_areas=attention_focus_areas,
                files=changed_files[:50],
                included_guidance=list(guidance_by_path.values())[:10],
                purpose="Current commit-to-commit change summary.",
            ),
        ]

        return [self._write_pack(repository.id, snapshot.id, pack) for pack in packs]

    def _pack_payload(
        self,
        *,
        pack_type: ContextPackType,
        snapshot_payload: dict[str, Any],
        repository: RepositoryRead,
        snapshot: RepoSnapshotRead,
        drift_event: DriftEventRead | None,
        guidance_sources: list[GuidanceSourceRead],
        changed_files: list[dict[str, Any]],
        attention_profile: AttentionProfileRead | None,
        attention_focus_areas: list[AttentionFocusAreaRead],
        files: list[dict[str, Any]],
        included_guidance: list[GuidanceSourceRead],
        purpose: str,
    ) -> dict[str, Any]:
        content_budget = PromptContentBudget(self.max_pack_content_bytes)
        included_files = self._with_file_content(
            snapshot_payload=snapshot_payload,
            files=files[:100],
            content_budget=content_budget,
        )
        included_guidance_payload = self._with_guidance_content(
            snapshot_payload=snapshot_payload,
            guidance_sources=included_guidance[:25],
            content_budget=content_budget,
        )
        payload = {
            "schema_version": 1,
            "pack_type": pack_type.value,
            "purpose": purpose,
            "repository": {
                "id": str(repository.id),
                "name": repository.name,
                "source_type": repository.source_type.value,
                "source_uri": repository.source_uri,
            },
            "snapshot": {
                "id": str(snapshot.id),
                "commit_sha": snapshot.commit_sha,
                "index_uri": snapshot.index_uri,
            },
            "drift": self._drift_payload(drift_event),
            "attention_profile": self._attention_profile_payload(attention_profile, attention_focus_areas),
            "included_files": included_files,
            "included_guidance": included_guidance_payload,
            "source_refs": self._source_refs(
                files=files,
                included_guidance=included_guidance,
                drift_event=drift_event,
                attention_profile=attention_profile,
            ),
            "summary": {
                "total_guidance_sources": len(guidance_sources),
                "total_changed_files": len(changed_files),
                "included_file_count": min(len(files), 100),
                "included_guidance_count": min(len(included_guidance), 25),
            },
            "prompt_content": {
                "max_total_bytes": content_budget.max_bytes,
                "used_bytes": content_budget.used_bytes,
                "remaining_bytes": content_budget.remaining_bytes,
                "included_excerpt_count": content_budget.included_excerpt_count,
                "omitted_excerpt_count": content_budget.omitted_excerpt_count,
                "max_code_excerpt_bytes": self.max_code_excerpt_bytes,
                "max_guidance_excerpt_bytes": self.max_guidance_excerpt_bytes,
            },
        }
        return payload

    def _with_file_content(
        self,
        *,
        snapshot_payload: dict[str, Any],
        files: list[dict[str, Any]],
        content_budget: PromptContentBudget,
    ) -> list[dict[str, Any]]:
        enriched_files: list[dict[str, Any]] = []
        for file in files:
            path = file.get("path")
            if not isinstance(path, str):
                enriched_files.append(file)
                continue

            enriched_files.append(
                {
                    **file,
                    "content_excerpt": self._content_excerpt(
                        snapshot_payload=snapshot_payload,
                        relative_path=path,
                        max_excerpt_bytes=self.max_code_excerpt_bytes,
                        content_budget=content_budget,
                    ),
                }
            )
        return enriched_files

    def _with_guidance_content(
        self,
        *,
        snapshot_payload: dict[str, Any],
        guidance_sources: list[GuidanceSourceRead],
        content_budget: PromptContentBudget,
    ) -> list[dict[str, Any]]:
        guidance_payloads: list[dict[str, Any]] = []
        for source in guidance_sources:
            guidance_payloads.append(
                {
                    "path": source.path,
                    "source_type": source.source_type.value,
                    "content_hash": source.content_hash,
                    "content_excerpt": self._content_excerpt(
                        snapshot_payload=snapshot_payload,
                        relative_path=source.path,
                        max_excerpt_bytes=self.max_guidance_excerpt_bytes,
                        content_budget=content_budget,
                    ),
                }
            )
        return guidance_payloads

    def _content_excerpt(
        self,
        *,
        snapshot_payload: dict[str, Any],
        relative_path: str,
        max_excerpt_bytes: int,
        content_budget: PromptContentBudget,
    ) -> dict[str, Any]:
        if self._is_prompt_unsafe_path(relative_path):
            content_budget.omit()
            return {"omitted": True, "omitted_reason": "unsafe_path"}

        if content_budget.remaining_bytes <= 0:
            content_budget.omit()
            return {"omitted": True, "omitted_reason": "prompt_budget_exhausted"}

        repository_path = self._resolved_repository_path(snapshot_payload)
        if repository_path is None:
            content_budget.omit()
            return {"omitted": True, "omitted_reason": "repository_path_unavailable"}

        source_path = self._safe_repository_file(repository_path, relative_path)
        if source_path is None or not source_path.is_file():
            content_budget.omit()
            return {"omitted": True, "omitted_reason": "file_unavailable"}

        byte_limit = min(max_excerpt_bytes, content_budget.remaining_bytes)
        if byte_limit <= 0:
            content_budget.omit()
            return {"omitted": True, "omitted_reason": "prompt_budget_exhausted"}

        with source_path.open("rb") as source_file:
            raw_excerpt = source_file.read(byte_limit + 1)
        if b"\0" in raw_excerpt:
            content_budget.omit()
            return {"omitted": True, "omitted_reason": "binary_file"}

        raw_excerpt = raw_excerpt[:byte_limit]
        text = raw_excerpt.decode("utf-8", errors="replace")
        text = self._redact_secret_lines(text)
        truncated = source_path.stat().st_size > byte_limit
        content_budget.consume(text)
        return {
            "omitted": False,
            "text": text,
            "start_line": 1,
            "end_line": max(len(text.splitlines()), 1),
            "truncated": truncated,
            "size_bytes": source_path.stat().st_size,
            "max_bytes": max_excerpt_bytes,
        }

    @staticmethod
    def _resolved_repository_path(snapshot_payload: dict[str, Any]) -> Path | None:
        resolved_path = snapshot_payload.get("resolved_path")
        if not isinstance(resolved_path, str):
            return None

        repository_path = Path(resolved_path).expanduser().resolve()
        if not repository_path.is_dir():
            return None
        return repository_path

    @staticmethod
    def _safe_repository_file(repository_path: Path, relative_path: str) -> Path | None:
        source_path = (repository_path / relative_path).resolve()
        if not source_path.is_relative_to(repository_path):
            return None
        return source_path

    @staticmethod
    def _is_prompt_unsafe_path(path: str) -> bool:
        normalized_path = Path(path)
        lower_parts = [part.lower() for part in normalized_path.parts]
        lower_name = normalized_path.name.lower()
        lower_suffix = normalized_path.suffix.lower()

        if lower_name in UNSAFE_PROMPT_PATH_NAMES:
            return True
        if lower_suffix in UNSAFE_PROMPT_EXTENSIONS or lower_suffix in BINARY_PROMPT_EXTENSIONS:
            return True
        return any(part in {"secrets", ".secrets"} for part in lower_parts)

    @staticmethod
    def _redact_secret_lines(text: str) -> str:
        redacted_lines = [
            "[redacted credential-looking line]" if SECRET_LINE_PATTERN.search(line) else line
            for line in text.splitlines()
        ]
        if text.endswith(("\n", "\r")):
            return "\n".join(redacted_lines) + "\n"
        return "\n".join(redacted_lines)

    def _write_pack(
        self,
        repository_id: UUID,
        snapshot_id: UUID,
        payload: dict[str, Any],
    ) -> BuiltContextPack:
        pack_type = ContextPackType(payload["pack_type"])
        pack_dir = self.cache_root / "repositories" / str(repository_id) / "context_packs" / str(snapshot_id)
        pack_dir.mkdir(parents=True, exist_ok=True)
        pack_path = pack_dir / f"{pack_type.value}.json"
        encoded = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        pack_path.write_bytes(encoded)
        content_hash = hashlib.sha256(encoded).hexdigest()
        return BuiltContextPack(
            pack_type=pack_type,
            artifact_uri=str(pack_path),
            content_hash=content_hash,
            sources=[
                ContextPackSourceInput(
                    source_type=ContextPackSourceType(source["source_type"]),
                    source_uri=source["source_uri"],
                    content_hash=source.get("content_hash"),
                )
                for source in payload["source_refs"]
            ],
        )

    @staticmethod
    def _read_snapshot(snapshot: RepoSnapshotRead) -> dict[str, Any]:
        return json.loads(Path(snapshot.index_uri).read_text(encoding="utf-8"))

    @classmethod
    def _changed_files(
        cls,
        snapshot_payload: dict[str, Any],
        drift_event: DriftEventRead | None,
    ) -> list[dict[str, Any]]:
        if drift_event is None:
            return []
        changed_files = drift_event.breakdown.get("changed_files", [])
        if not isinstance(changed_files, list):
            return []
        files_by_path = {
            str(file.get("path")): file
            for file in cls._all_files(snapshot_payload)
            if file.get("path") is not None
        }

        enriched_files: list[dict[str, Any]] = []
        for file in changed_files:
            if not isinstance(file, dict):
                continue
            path = str(file.get("path", ""))
            snapshot_file = files_by_path.get(path, {})
            enriched_files.append({**snapshot_file, **file})
        return enriched_files

    @staticmethod
    def _all_files(snapshot_payload: dict[str, Any]) -> list[dict[str, Any]]:
        files = snapshot_payload.get("files", [])
        if not isinstance(files, list):
            return []
        return [file for file in files if isinstance(file, dict)]

    def _high_level_files(
        self,
        snapshot_payload: dict[str, Any],
        changed_files: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        all_files = self._all_files(snapshot_payload)
        important = [
            file
            for file in all_files
            if self._is_config_or_design_path(str(file.get("path", "")))
        ]
        return self._dedupe_files([*changed_files, *important])

    def _low_level_files(
        self,
        snapshot_payload: dict[str, Any],
        changed_files: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if changed_files:
            return changed_files
        return self._all_files(snapshot_payload)[:50]

    def _design_decision_files(
        self,
        snapshot_payload: dict[str, Any],
        changed_files: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        design_files = [
            file
            for file in self._all_files(snapshot_payload)
            if self._is_design_path(str(file.get("path", "")))
        ]
        return self._dedupe_files([*changed_files, *design_files])

    @staticmethod
    def _future_improvement_files(
        snapshot_payload: dict[str, Any],
        changed_files: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        risk_files = [
            file
            for file in changed_files
            if str(file.get("status", "")).startswith(("D", "R")) or file.get("config") is True
        ]
        if risk_files:
            return risk_files
        files = snapshot_payload.get("files", [])
        if not isinstance(files, list):
            return []
        return [file for file in files if isinstance(file, dict)][:25]

    @staticmethod
    def _dedupe_files(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for file in files:
            path = str(file.get("path", ""))
            if not path or path in seen:
                continue
            seen.add(path)
            deduped.append(file)
        return deduped

    @staticmethod
    def _is_config_or_design_path(path: str) -> bool:
        lower_path = path.lower()
        return (
            lower_path in {"readme.md", "dockerfile", "compose.yaml", "compose.yml", "pyproject.toml"}
            or lower_path.startswith((".design/", "docs/", "adr/", "adrs/", ".github/"))
        )

    @staticmethod
    def _is_design_path(path: str) -> bool:
        lower_path = path.lower()
        return lower_path.startswith((".design/", "docs/", "adr/", "adrs/")) or lower_path in {
            "agents.md",
            "claude.md",
            "readme.md",
        }

    @staticmethod
    def _drift_payload(drift_event: DriftEventRead | None) -> dict[str, Any] | None:
        if drift_event is None:
            return None
        return {
            "id": str(drift_event.id),
            "from_commit_sha": drift_event.from_commit_sha,
            "to_commit_sha": drift_event.to_commit_sha,
            "drift_score": str(drift_event.drift_score),
            "severity": drift_event.severity.value,
            "breakdown": drift_event.breakdown,
        }

    @staticmethod
    def _attention_profile_payload(
        attention_profile: AttentionProfileRead | None,
        attention_focus_areas: list[AttentionFocusAreaRead],
    ) -> dict[str, Any] | None:
        if attention_profile is None:
            return None
        return {
            "id": str(attention_profile.id),
            "name": attention_profile.name,
            "default_weight": str(attention_profile.default_weight),
            "focus_areas": [
                {
                    "id": str(focus_area.id),
                    "name": focus_area.name,
                    "weight": str(focus_area.weight),
                    "path_globs": focus_area.path_globs,
                }
                for focus_area in attention_focus_areas
            ],
        }

    @staticmethod
    def _source_refs(
        *,
        files: list[dict[str, Any]],
        included_guidance: list[GuidanceSourceRead],
        drift_event: DriftEventRead | None,
        attention_profile: AttentionProfileRead | None,
    ) -> list[dict[str, str | None]]:
        refs: list[dict[str, str | None]] = [
            {
                "source_type": ContextPackSourceType.CODE.value,
                "source_uri": f"code:{file.get('path')}",
                "content_hash": file.get("sha256"),
            }
            for file in files[:100]
            if file.get("path")
        ]
        refs.extend(
            {
                "source_type": ContextPackSourceType.GUIDANCE.value,
                "source_uri": f"guidance:{source.path}",
                "content_hash": source.content_hash,
            }
            for source in included_guidance[:25]
        )
        if drift_event is not None:
            refs.append(
                {
                    "source_type": ContextPackSourceType.DRIFT.value,
                    "source_uri": f"drift_event:{drift_event.id}",
                    "content_hash": None,
                }
            )
        if attention_profile is not None:
            refs.append(
                {
                    "source_type": ContextPackSourceType.ATTENTION_PROFILE.value,
                    "source_uri": f"attention_profile:{attention_profile.id}",
                    "content_hash": None,
                }
            )
        return refs
