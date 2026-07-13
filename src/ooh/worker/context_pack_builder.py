from __future__ import annotations

import hashlib
import json
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


@dataclass(frozen=True)
class BuiltContextPack:
    pack_type: ContextPackType
    artifact_uri: str
    content_hash: str
    sources: list[ContextPackSourceInput]


class ContextPackBuilder:
    def __init__(self, *, cache_root: Path) -> None:
        self.cache_root = cache_root

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
            "included_files": files[:100],
            "included_guidance": [
                {
                    "path": source.path,
                    "source_type": source.source_type.value,
                    "content_hash": source.content_hash,
                }
                for source in included_guidance[:25]
            ],
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
        }
        return payload

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
