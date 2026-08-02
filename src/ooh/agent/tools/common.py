from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

DEFAULT_LIST_FILES_LIMIT = 500
DEFAULT_READ_FILE_RANGE_LINES = 200
DEFAULT_READ_FILE_RANGE_BYTES = 24_000
DEFAULT_SEARCH_RESULT_LIMIT = 50
DEFAULT_SEARCH_FILE_BYTES = 200_000
DEFAULT_SEARCH_LINE_BYTES = 500
DEFAULT_SYMBOL_LIMIT = 50
DEFAULT_SYMBOL_READ_BYTES = 32_000

UNSAFE_TOOL_PATH_NAMES = {
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

UNSAFE_TOOL_EXTENSIONS = {
    ".crt",
    ".der",
    ".key",
    ".pem",
    ".pfx",
    ".p12",
}

BINARY_TOOL_EXTENSIONS = {
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


class RepoToolError(ValueError):
    pass


@dataclass(frozen=True)
class RepoToolResult:
    tool_name: str
    payload: dict[str, Any]
    evidence_refs: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "payload": self.payload,
            "evidence_refs": self.evidence_refs,
        }


@dataclass(frozen=True)
class RepositoryToolContext:
    index_uri: str
    repository_id: str
    commit_sha: str
    repository_path: Path
    files: list[dict[str, Any]]
    symbols: list[dict[str, Any]]
    imports: list[dict[str, Any]]
    snapshot_id: str | None = None

    @classmethod
    def from_index_uri(
        cls,
        index_uri: str | Path,
        *,
        snapshot_id: str | None = None,
    ) -> "RepositoryToolContext":
        manifest_path = Path(index_uri)
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise RepoToolError(f"snapshot manifest is not readable: {manifest_path}") from exc
        except json.JSONDecodeError as exc:
            raise RepoToolError(f"snapshot manifest is not valid JSON: {manifest_path}") from exc

        if not isinstance(manifest, dict):
            raise RepoToolError("snapshot manifest must be a JSON object")

        repository_path = resolved_repository_path(manifest)
        repository_id = require_string(manifest, "repository_id")
        commit_sha = require_string(manifest, "commit_sha")

        return cls(
            index_uri=str(manifest_path),
            repository_id=repository_id,
            commit_sha=commit_sha,
            repository_path=repository_path,
            files=read_index_artifact(manifest, artifact_name="files", item_key="files"),
            symbols=read_index_artifact(manifest, artifact_name="symbols", item_key="symbols"),
            imports=read_index_artifact(manifest, artifact_name="imports", item_key="imports"),
            snapshot_id=snapshot_id,
        )

    def file_by_path(self, path: str) -> dict[str, Any]:
        relative_path = normalize_relative_path(path)
        files_by_path = {
            file["path"]: file
            for file in self.files
            if isinstance(file.get("path"), str)
        }
        file = files_by_path.get(relative_path)
        if file is None:
            raise RepoToolError(f"snapshot does not include file: {relative_path}")
        return file

    def source_path_for_file(self, path: str) -> Path:
        relative_path = normalize_relative_path(path)
        source_path = (self.repository_path / relative_path).resolve()
        if not source_path.is_relative_to(self.repository_path):
            raise RepoToolError(f"path escapes repository root: {relative_path}")
        return source_path


def resolved_repository_path(manifest: dict[str, Any]) -> Path:
    resolved_path = require_string(manifest, "resolved_path")
    repository_path = Path(resolved_path).expanduser().resolve()
    if not repository_path.is_dir():
        raise RepoToolError(f"repository path is unavailable: {repository_path}")
    return repository_path


def read_index_artifact(
    manifest: dict[str, Any],
    *,
    artifact_name: str,
    item_key: str,
) -> list[dict[str, Any]]:
    index = manifest.get("index")
    if not isinstance(index, dict):
        raise RepoToolError("snapshot does not include structural index metadata")

    artifacts = index.get("artifacts")
    if not isinstance(artifacts, dict):
        raise RepoToolError("snapshot does not include structural index artifacts")

    artifact = artifacts.get(artifact_name)
    if not isinstance(artifact, dict):
        raise RepoToolError(f"snapshot is missing index artifact: {artifact_name}")

    uri = artifact.get("uri")
    if not isinstance(uri, str) or not uri:
        raise RepoToolError(f"snapshot index artifact has no uri: {artifact_name}")

    artifact_path = Path(uri)
    try:
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RepoToolError(f"snapshot index artifact is not readable: {artifact_path}") from exc
    except json.JSONDecodeError as exc:
        raise RepoToolError(f"snapshot index artifact is not valid JSON: {artifact_path}") from exc

    if not isinstance(payload, dict):
        raise RepoToolError(f"snapshot index artifact must be a JSON object: {artifact_name}")

    items = payload.get(item_key)
    if not isinstance(items, list):
        raise RepoToolError(f"snapshot index artifact is missing list: {item_key}")
    return [item for item in items if isinstance(item, dict)]


def require_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise RepoToolError(f"snapshot manifest is missing string field: {key}")
    return value


def normalize_relative_path(path: str) -> str:
    if "\0" in path:
        raise RepoToolError("path contains a null byte")

    raw_path = path.replace("\\", "/").strip()
    candidate = PurePosixPath(raw_path)
    if candidate.is_absolute():
        raise RepoToolError(f"path must be repository-relative: {path}")

    parts = [part for part in candidate.parts if part not in {"", "."}]
    if not parts:
        raise RepoToolError("path must not be empty")
    if any(part == ".." for part in parts):
        raise RepoToolError(f"path must not contain traversal segments: {path}")
    if parts[0] == "~":
        raise RepoToolError(f"path must be repository-relative: {path}")

    return "/".join(parts)


def normalize_relative_prefix(path_prefix: str | None) -> str | None:
    if path_prefix is None or path_prefix.strip() in {"", "."}:
        return None
    return normalize_relative_path(path_prefix).rstrip("/")


def bounded_limit(value: int | None, *, default: int, maximum: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise RepoToolError("limit must be an integer")
    if value <= 0:
        raise RepoToolError("limit must be positive")
    return min(value, maximum)


def validate_line_number(value: int, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RepoToolError(f"{name} must be an integer")
    if value <= 0:
        raise RepoToolError(f"{name} must be positive")
    return value


def validate_positive_int(value: int, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RepoToolError(f"{name} must be an integer")
    if value <= 0:
        raise RepoToolError(f"{name} must be positive")
    return value


def file_summary(file: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": file.get("path"),
        "size_bytes": file.get("size_bytes"),
        "sha256": file.get("sha256"),
        "language": file.get("language"),
        "line_count": file.get("line_count"),
        "parse_status": file.get("parse_status"),
        "parse_error_count": file.get("parse_error_count"),
    }


def symbol_summary(symbol: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": symbol.get("path"),
        "language": symbol.get("language"),
        "kind": symbol.get("kind"),
        "name": symbol.get("name"),
        "qualified_name": symbol.get("qualified_name"),
        "parent_qualified_name": symbol.get("parent_qualified_name"),
        "start_line": symbol.get("start_line"),
        "end_line": symbol.get("end_line"),
        "signature": symbol.get("signature"),
    }


def sort_symbols(symbols: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        symbols,
        key=lambda symbol: (
            str(symbol.get("path", "")),
            int(symbol.get("start_line", 0) or 0),
            str(symbol.get("qualified_name", "")),
        ),
    )


def read_source_excerpt(
    context: RepositoryToolContext,
    *,
    path: str,
    start_line: int,
    end_line: int,
    tool_name: str,
    max_lines: int = DEFAULT_READ_FILE_RANGE_LINES,
    max_bytes: int = DEFAULT_READ_FILE_RANGE_BYTES,
    qualified_name: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    start_line = validate_line_number(start_line, name="start_line")
    end_line = validate_line_number(end_line, name="end_line")
    max_lines = validate_positive_int(max_lines, name="max_lines")
    max_bytes = validate_positive_int(max_bytes, name="max_bytes")
    if end_line < start_line:
        raise RepoToolError("end_line must be greater than or equal to start_line")

    relative_path = normalize_relative_path(path)
    if is_unsafe_path(relative_path):
        raise RepoToolError(f"refusing to read unsafe path: {relative_path}")

    file = context.file_by_path(relative_path)
    source_path = context.source_path_for_file(relative_path)
    if not source_path.is_file():
        raise RepoToolError(f"repository file is unavailable: {relative_path}")

    line_count = int(file.get("line_count", 0) or 0)
    if line_count == 0:
        return empty_excerpt_payload(file, tool_name, qualified_name)
    if start_line > line_count:
        raise RepoToolError(f"start_line exceeds file line count: {start_line} > {line_count}")

    requested_end = min(end_line, line_count)
    target_end = min(requested_end, start_line + max_lines - 1)
    lines: list[dict[str, Any]] = []
    used_bytes = 0
    bytes_truncated = False

    with source_path.open("r", encoding="utf-8", errors="replace") as source_file:
        for line_number, raw_line in enumerate(source_file, start=1):
            if line_number < start_line:
                continue
            if line_number > target_end:
                break

            text = raw_line.rstrip("\n\r")
            if "\0" in text:
                raise RepoToolError(f"refusing to read binary-looking file: {relative_path}")

            redacted_text = redact_secret_line(text)
            line_separator_bytes = 1 if lines else 0
            line_bytes = len(redacted_text.encode("utf-8")) + line_separator_bytes
            if used_bytes + line_bytes > max_bytes:
                remaining_bytes = max(max_bytes - used_bytes - line_separator_bytes, 0)
                if remaining_bytes > 0:
                    redacted_text = truncate_to_bytes(redacted_text, remaining_bytes)
                    lines.append({"line_number": line_number, "text": redacted_text})
                bytes_truncated = True
                break

            lines.append({"line_number": line_number, "text": redacted_text})
            used_bytes += line_bytes

    actual_end_line = lines[-1]["line_number"] if lines else start_line - 1
    line_truncated = actual_end_line < requested_end
    content = "\n".join(str(line["text"]) for line in lines)
    payload = {
        "path": relative_path,
        "start_line": start_line,
        "end_line": actual_end_line,
        "requested_end_line": end_line,
        "content": content,
        "lines": lines,
        "truncated": line_truncated or bytes_truncated,
        "truncated_reason": truncated_reason(line_truncated, bytes_truncated),
        "sha256": file.get("sha256"),
        "line_count": line_count,
        "max_lines": max_lines,
        "max_bytes": max_bytes,
    }
    evidence_ref = code_evidence_ref(
        context,
        file=file,
        start_line=start_line,
        end_line=actual_end_line,
        tool_name=tool_name,
        qualified_name=qualified_name,
    )
    return payload, evidence_ref


def empty_excerpt_payload(
    file: dict[str, Any],
    tool_name: str,
    qualified_name: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = {
        "path": file.get("path"),
        "start_line": 1,
        "end_line": 0,
        "requested_end_line": 1,
        "content": "",
        "lines": [],
        "truncated": False,
        "truncated_reason": None,
        "sha256": file.get("sha256"),
        "line_count": 0,
        "max_lines": DEFAULT_READ_FILE_RANGE_LINES,
        "max_bytes": DEFAULT_READ_FILE_RANGE_BYTES,
    }
    evidence_ref = {
        "source_type": "code",
        "source_uri": f"code:{file.get('path')}",
        "content_hash": file.get("sha256"),
        "metadata": {
            "tool": tool_name,
            "start_line": 1,
            "end_line": 0,
            **({"qualified_name": qualified_name} if qualified_name is not None else {}),
        },
    }
    return payload, evidence_ref


def code_evidence_ref(
    context: RepositoryToolContext,
    *,
    file: dict[str, Any],
    start_line: int,
    end_line: int,
    tool_name: str,
    qualified_name: str | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "commit_sha": context.commit_sha,
        "start_line": start_line,
        "end_line": end_line,
        "tool": tool_name,
    }
    if context.snapshot_id is not None:
        metadata["snapshot_id"] = context.snapshot_id
    if qualified_name is not None:
        metadata["qualified_name"] = qualified_name

    return {
        "source_type": "code",
        "source_uri": f"code:{file.get('path')}",
        "content_hash": file.get("sha256"),
        "metadata": metadata,
    }


def is_unsafe_path(path: str) -> bool:
    normalized_path = PurePosixPath(path)
    lower_parts = [part.lower() for part in normalized_path.parts]
    lower_name = normalized_path.name.lower()
    lower_suffix = normalized_path.suffix.lower()

    if lower_name in UNSAFE_TOOL_PATH_NAMES:
        return True
    if lower_suffix in UNSAFE_TOOL_EXTENSIONS or lower_suffix in BINARY_TOOL_EXTENSIONS:
        return True
    return any(part in {"secrets", ".secrets"} for part in lower_parts)


def redact_secret_line(text: str) -> str:
    if SECRET_LINE_PATTERN.search(text):
        return "[redacted credential-looking line]"
    return text


def truncate_to_bytes(text: str, max_bytes: int) -> str:
    if max_bytes <= 0:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def truncated_reason(line_truncated: bool, bytes_truncated: bool) -> str | None:
    if line_truncated and bytes_truncated:
        return "line_and_byte_limit"
    if line_truncated:
        return "line_limit"
    if bytes_truncated:
        return "byte_limit"
    return None
