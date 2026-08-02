from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class StoredAgentArtifact:
    artifact_uri: str
    content_hash: str


class AgentArtifactStore:
    def __init__(self, *, cache_root: Path) -> None:
        self.cache_root = cache_root

    def write_json(
        self,
        *,
        agent_run_id: UUID,
        file_name: str,
        payload: dict[str, Any],
    ) -> StoredAgentArtifact:
        artifact_dir = self.cache_root / "agent_runs" / str(agent_run_id)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / file_name
        encoded = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        artifact_path.write_bytes(encoded)
        return StoredAgentArtifact(
            artifact_uri=str(artifact_path),
            content_hash=hashlib.sha256(encoded).hexdigest(),
        )
